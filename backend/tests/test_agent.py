"""Agent loop tests with a scripted model standing in for Claude; tools run against real Postgres."""

from types import SimpleNamespace

import pytest

from app.agent.loop import echo_content, run_agent
from app.agent.single_shot import run_rag
from app.agent.tools import TOOLS, ToolContext, run_tool
from app.retrieval.embeddings import NoopReranker


def test_tool_schemas_are_plain_json_schema():
    names = {t["name"] for t in TOOLS}
    assert names == {"repo_overview", "search_code", "read_file", "grep", "find_symbol", "list_endpoints",
                     "list_dependencies", "find_imports"}
    for tool in TOOLS:
        schema = tool["input_schema"]
        assert schema["type"] == "object"
        assert schema.get("additionalProperties") is False
        assert tool["eager_input_streaming"] is True
        for prop in schema.get("properties", {}).values():
            assert "anyOf" not in prop and "title" not in prop


def test_echo_content_drops_declined_blocks_before_fallback_marker():
    blocks = [
        {"type": "thinking", "thinking": "", "signature": "a"},
        {"type": "text", "text": "Partial"},
        {"type": "tool_use", "id": "t0", "name": "grep", "input": {}},
        {"type": "fallback", "from": {"model": "x"}, "to": {"model": "y"}},
        {"type": "text", "text": "continued"},
        {"type": "tool_use", "id": "t1", "name": "grep", "input": {"pattern": "x"}},
    ]
    assert [b["type"] for b in echo_content(blocks)] == ["text", "text", "tool_use"]
    assert echo_content(blocks[:2]) == blocks[:2]


class ScriptedModel:
    """Plays back a list of assistant turns and records the messages it was sent."""

    def __init__(self, turns: list[list[dict]]):
        self.turns = turns
        self.calls: list[dict] = []

    async def stream(self, messages, *, allow_tools):
        self.calls.append({"messages": [dict(m) for m in messages], "allow_tools": allow_tools})
        content = self.turns[len(self.calls) - 1]
        for block in content:
            if block["type"] == "text":
                yield "text", block["text"]
            elif block["type"] == "tool_use":
                yield "tool_start", block["name"]
        stop = "tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn"
        usage = SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        yield "final", SimpleNamespace(content=content, stop_reason=stop, usage=usage, stop_details=None,
                                       model="scripted-model")


async def _collect(gen):
    return [event async for event in gen]


@pytest.fixture(scope="module")
def ctx(indexed_repo):
    repo_id, stats, embedder = indexed_repo
    repo = {"id": repo_id, "name": "sample", "source": "fixture", "commit_sha": None, "stats": stats}
    return ToolContext(repo=repo, embedder=embedder, reranker=NoopReranker())


@pytest.mark.integration
async def test_agent_runs_tools_and_streams_answer(ctx):
    model = ScriptedModel([
        [{"type": "tool_use", "id": "t1", "name": "list_endpoints", "input": {"route_contains": "owners"}},
         {"type": "tool_use", "id": "t2", "name": "find_symbol", "input": {"name": "showOwner"}}],
        [{"type": "text", "text": "Owners are shown by `showOwner` "},
         {"type": "text", "text": "[svc/src/main/java/com/example/petclinic/OwnerController.java:33-36]."}],
    ])
    events = await _collect(run_agent("Which endpoint shows an owner?", [], ctx, model, max_steps=5))
    types = [e["type"] for e in events]
    assert types.count("tool_result") == 2
    assert "".join(e["delta"] for e in events if e["type"] == "text").startswith("Owners are shown")
    assert types[-1] == "done" and events[-1]["usage"]["input_tokens"] == 20

    results = [e for e in events if e["type"] == "tool_result"]
    assert "GET     /owners/{ownerId}" in model.calls[1]["messages"][-1]["content"][0]["content"]
    assert any(s["path"].endswith("OwnerController.java") for r in results for s in r["sources"])

    saved = next(e for e in events if e["type"] == "_messages")["messages"]
    assert [m["role"] for m in saved] == ["user", "assistant", "user", "assistant"]


@pytest.mark.integration
async def test_agent_stops_calling_tools_at_step_limit(ctx):
    loop_turn = [{"type": "tool_use", "id": "t", "name": "grep", "input": {"pattern": "Item"}}]
    model = ScriptedModel([loop_turn, loop_turn, [{"type": "text", "text": "Done."}]])
    events = await _collect(run_agent("q", [], ctx, model, max_steps=2))
    assert model.calls[-1]["allow_tools"] is False
    last_user = model.calls[-1]["messages"][-1]
    assert last_user["content"][-1]["type"] == "text" and "tool-call limit" in last_user["content"][-1]["text"]
    assert events[-1]["type"] == "done"


@pytest.mark.integration
async def test_invalid_tool_input_returns_error_result(ctx):
    output = await run_tool(ctx, "read_file", {"path": 7, "extra": True})
    assert output.is_error and "Invalid arguments" in output.text
    output = await run_tool(ctx, "read_file", "not-json")
    assert output.is_error and "INVALID_JSON" in output.text


@pytest.mark.integration
async def test_read_file_suggests_similar_paths(ctx):
    output = await run_tool(ctx, "read_file", {"path": "items.py"})
    assert output.is_error and "api/routes/items.py" in output.text
    output = await run_tool(ctx, "read_file", {"path": "api/routes/items.py", "start_line": 11, "end_line": 14})
    assert output.text.splitlines()[1].strip().startswith("11  @router.get")


@pytest.mark.integration
async def test_grep_find_imports_dependencies_and_overview(ctx):
    grep = await run_tool(ctx, "grep", {"pattern": r"session\.commit\(", "path_glob": "*.py"})
    assert grep.text.count("api/routes/items.py:") == 2
    imports = await run_tool(ctx, "find_imports", {"module_contains": "gin"})
    assert "gosvc/main.go" in imports.text
    deps = await run_tool(ctx, "list_dependencies", {"ecosystem": "maven"})
    assert "junit-jupiter" in deps.text and "[test]" in deps.text
    overview = await run_tool(ctx, "repo_overview", {})
    assert "Endpoints:" in overview.text and "README.md" in overview.text


@pytest.mark.integration
async def test_done_event_reports_served_models_and_tool_calls(ctx):
    model = ScriptedModel([
        [{"type": "tool_use", "id": "t1", "name": "find_symbol", "input": {"name": "showOwner"}}],
        [{"type": "text", "text": "Done."}],
    ])
    events = await _collect(run_agent("q", [], ctx, model, max_steps=5))
    done = events[-1]
    assert done["models"] == ["scripted-model", "scripted-model"]
    assert done["tool_calls"] == 1 and done["stop_reason"] == "end_turn"


@pytest.mark.integration
async def test_single_shot_rag_retrieves_once_and_answers_without_tools(ctx):
    model = ScriptedModel([[{"type": "text", "text": "Archived via [api/routes/items.py:42-45]."}]])
    events = await _collect(run_rag("How is an item archived?", ctx, model, k=3))
    retrieval = next(e for e in events if e["type"] == "retrieval")
    assert retrieval["sources"] and len(retrieval["sources"]) <= 3
    assert model.calls[0]["allow_tools"] is False
    prompt = model.calls[0]["messages"][0]["content"]
    assert "<excerpts>" in prompt and "Question: How is an item archived?" in prompt
    assert events[-1]["type"] == "done" and events[-1]["tool_calls"] == 0
