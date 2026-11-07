"""Observability: JSON logs and request IDs (unit), the answer log, /stats and /answers, and the span tree
(integration, with a scripted model standing in for Claude)."""

import json
import logging

import httpx
import pytest
from fastapi import FastAPI
from mcp.client import Client
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.agent import answer
from app.agent.answer import answer_question
from app.config import get_settings
from app.db import connection
from app.ingest.pipeline import index_repo
from app.main import app as api_app
from app.mcp_server import server as mcp_server
from app.observability.answers import observe_answer
from app.observability.logs import JsonFormatter, RequestLogMiddleware, request_id_var
from tests.test_agent import ScriptedModel

TOOL_TURN = [{"type": "text", "text": "Looking it up. "},
             {"type": "tool_use", "id": "t1", "name": "find_symbol", "input": {"name": "ItemService"}}]
ANSWER_TURN = [{"type": "text", "text": "ItemService is in [api/routes/items.py:36-45]."}]


class PricedModel(ScriptedModel):
    """ScriptedModel reporting a real model id, so the answer gets a price."""

    async def stream(self, messages, *, allow_tools):
        async for kind, payload in super().stream(messages, allow_tools=allow_tools):
            if kind == "final":
                payload.model = "claude-haiku-5-5"
            yield kind, payload


class FailingModel:
    async def stream(self, messages, *, allow_tools):
        raise RuntimeError("upstream exploded")
        yield  # pragma: no cover  (makes this an async generator)


@pytest.fixture(scope="module")
def spans() -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return exporter


@pytest.fixture(scope="module")
def repo(indexed_repo) -> dict:
    repo_id, stats, _ = indexed_repo
    return {"id": repo_id, "name": "sample", "source": "fixture", "commit_sha": None, "stats": stats}


def use_model(monkeypatch, model) -> None:
    monkeypatch.setattr(answer, "ClaudeModel", lambda settings, system, client=None, tools=None: model)


async def collect(events) -> list[dict]:
    return [event async for event in events]


async def run_row(run_id: int) -> dict:
    async with connection() as conn:
        return await (await conn.execute("SELECT * FROM answer_runs WHERE id = %s", [run_id])).fetchone()


async def latest_row(repo_id: int, source: str) -> dict:
    async with connection() as conn:
        return await (await conn.execute(
            "SELECT * FROM answer_runs WHERE repo_id = %s AND source = %s ORDER BY id DESC LIMIT 1",
            [repo_id, source])).fetchone()


# --- logs ------------------------------------------------------------------------------------------

def test_json_formatter_carries_request_id_and_extra_fields():
    record = logging.LogRecord("codedoc.test", logging.INFO, __file__, 1, "answer.finished", None, None)
    record.repo_id, record.latency_ms = 4, 6120
    token = request_id_var.set("req-1")
    try:
        entry = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)
    assert entry["msg"] == "answer.finished" and entry["level"] == "INFO" and entry["logger"] == "codedoc.test"
    assert entry["request_id"] == "req-1" and entry["repo_id"] == 4 and entry["latency_ms"] == 6120
    assert entry["ts"].endswith("Z") and "trace_id" not in entry


async def test_request_middleware_echoes_the_id_and_logs_one_line(caplog):
    app = FastAPI()

    @app.get("/ping")
    async def ping() -> dict:
        return {"request_id": request_id_var.get()}

    app.add_middleware(RequestLogMiddleware)
    caplog.set_level(logging.INFO, logger="codedoc.http")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        given = await client.get("/ping", headers={"X-Request-ID": "abc-123"})
        made = await client.get("/ping", headers={"X-Request-ID": "not valid!"})
    assert given.headers["x-request-id"] == "abc-123" and given.json() == {"request_id": "abc-123"}
    assert made.headers["x-request-id"] != "not valid!" and len(made.headers["x-request-id"]) == 16
    lines = [r for r in caplog.records if r.msg == "http.request"]
    assert len(lines) == 2
    assert (lines[0].method, lines[0].path, lines[0].status) == ("GET", "/ping", 200)
    assert lines[0].duration_ms >= 0


# --- the answer log --------------------------------------------------------------------------------

@pytest.mark.integration
async def test_answer_is_recorded_with_tools_tokens_and_cost(repo, monkeypatch, spans):
    use_model(monkeypatch, PricedModel([TOOL_TURN, ANSWER_TURN]))
    spans.clear()
    events = await collect(observe_answer(
        answer_question("Where is ItemService?", repo, settings=get_settings()),
        source="web", repo=repo, question="Where is ItemService?", mode="agent", settings=get_settings()))

    assert not [e for e in events if e["type"].startswith("_") and e["type"] not in {"_system", "_messages", "_step"}]
    tool_result = next(e for e in events if e["type"] == "tool_result")
    assert isinstance(tool_result["ms"], int)
    done = events[-1]
    assert done["type"] == "done" and done["latency_ms"] >= done["ttft_ms"] >= 0
    # Two Haiku calls of 10 input + 5 output tokens: 2 * (10 * $0.10 + 5 * $0.50) / 1M
    assert done["cost_usd"] == pytest.approx(0.000007)

    row = await latest_row(repo["id"], "web")
    assert (row["status"], row["steps"], row["tool_calls"], row["tool_errors"]) == ("ok", 2, 1, 0)
    assert (row["input_tokens"], row["output_tokens"], row["models"]) == (20, 10, ["claude-haiku-5-5"] * 2)
    assert row["question"] == "Where is ItemService?" and row["answer"].startswith("ItemService is in")
    assert row["tools"][0]["name"] == "find_symbol" and row["tools"][0]["sources"] >= 1
    assert [s["kind"] for s in row["step_log"]] == ["model", "model"]
    assert row["cost_usd"] == pytest.approx(0.000007)
    assert row["trace_id"] and len(row["trace_id"]) == 32

    # The span tree: answer -> chat (x2) and answer -> execute_tool, with token attributes on chat spans.
    finished = {s.name: s for s in spans.get_finished_spans()}
    root = finished["answer"]
    assert format(root.context.trace_id, "032x") == row["trace_id"]
    chats = [s for s in spans.get_finished_spans() if s.name == "chat"]
    assert len(chats) == 2 and all(s.parent.span_id == root.context.span_id for s in chats)
    assert chats[0].attributes["gen_ai.usage.output_tokens"] == 5
    assert chats[0].attributes["gen_ai.response.model"] == "claude-haiku-5-5"
    tool = finished["execute_tool find_symbol"]
    assert tool.parent.span_id == root.context.span_id and tool.attributes["gen_ai.tool.name"] == "find_symbol"
    assert root.attributes["codedoc.status"] == "ok"


@pytest.mark.integration
async def test_failed_answer_is_recorded_as_an_error(repo, monkeypatch):
    use_model(monkeypatch, FailingModel())
    with pytest.raises(RuntimeError, match="upstream exploded"):
        await collect(observe_answer(
            answer_question("Anything?", repo, settings=get_settings()),
            source="web", repo=repo, question="Anything?", mode="agent", settings=get_settings()))
    row = await latest_row(repo["id"], "web")
    assert row["status"] == "error" and row["error"] == "RuntimeError: upstream exploded"
    assert row["steps"] == 0 and row["cost_usd"] == 0


@pytest.mark.integration
async def test_ask_codebase_is_recorded_as_mcp(repo, monkeypatch):
    use_model(monkeypatch, PricedModel([ANSWER_TURN]))
    async with Client(mcp_server) as client:
        result = await client.call_tool("ask_codebase", {"repo": repo["id"], "question": "Where is ItemService?"})
    assert not result.is_error and "ItemService is in" in result.content[0].text
    row = await latest_row(repo["id"], "mcp")
    assert (row["status"], row["mode"], row["steps"]) == ("ok", "agent", 1)


@pytest.mark.integration
async def test_stats_and_answers_endpoints(repo, monkeypatch):
    use_model(monkeypatch, PricedModel([TOOL_TURN, ANSWER_TURN]))
    await collect(observe_answer(
        answer_question("Where is ItemService?", repo, settings=get_settings()),
        source="web", repo=repo, question="Where is ItemService?", mode="agent", settings=get_settings()))

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api_app), base_url="http://test") as client:
        stats = (await client.get("/stats", params={"repo_id": repo["id"], "days": 1})).json()
        runs = (await client.get("/answers", params={"repo_id": repo["id"], "limit": 5})).json()
        detail = (await client.get(f"/answers/{runs[0]['id']}")).json()
        missing = await client.get("/answers/999999999")

    assert stats["answers"] >= 2 and stats["ok"] >= 1 and stats["cost_usd"] > 0
    assert stats["latency_p50_ms"] is not None and stats["per_day"][0]["answers"] == stats["answers"]
    haiku = next(m for m in stats["by_model"] if m["model"] == "claude-haiku-5-5")
    assert haiku["answers"] >= 1
    find_symbol = next(t for t in stats["tools"] if t["name"] == "find_symbol")
    assert find_symbol["calls"] >= 1 and find_symbol["error_rate"] == 0
    assert runs[0]["question"] == "Where is ItemService?" and runs[0]["model"] == "claude-haiku-5-5"
    assert detail["tools"][0]["name"] == "find_symbol" and detail["answer"].startswith("ItemService")
    assert missing.status_code == 404


# --- indexing ---------------------------------------------------------------------------------------

@pytest.mark.integration
async def test_indexing_records_phase_timings_and_spans(indexed_repo, spans):
    repo_id, _, embedder = indexed_repo
    spans.clear()
    stats = await index_repo(repo_id, embedder=embedder)
    assert set(stats["timings"]) == {"fetch", "parse", "write", "embed", "graph"}
    finished = {s.name: s for s in spans.get_finished_spans()}
    root = finished["index_repo"]
    for phase in ("fetch", "parse", "write", "embed", "graph"):
        assert finished[f"index.{phase}"].parent.span_id == root.context.span_id
    assert root.attributes["codedoc.chunks"] == stats["chunks"]
    assert finished["index.graph"].attributes["codedoc.graph.nodes"] == stats["graph"]["nodes"]
