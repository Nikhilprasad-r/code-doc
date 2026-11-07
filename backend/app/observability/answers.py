"""The answer log: one `answer_runs` row per answer, from the web chat or MCP `ask_codebase`.

`observe_answer` wraps the event stream of `answer_question`. It passes every event through, adds
`latency_ms`, `ttft_ms` and `cost_usd` to the final `done` event, and when the stream ends (normally, with an
error, or because the client went away) it writes the row, logs `answer.finished`, and closes the
`answer` span that the Claude calls and tool calls nest under.
"""

import asyncio
import json
import logging
import time
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import anyio
from opentelemetry.trace import Status, StatusCode

from app.config import Settings
from app.db import connection
from app.observability.tracing import current_trace_id, tracer
from app.pricing import cost_usd

log = logging.getLogger("codedoc.answers")

TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


@dataclass
class AnswerRun:
    source: str
    repo_id: int
    mode: str
    question: str
    requested_model: str
    conversation_id: str | None = None
    started: float = field(default_factory=time.perf_counter)
    ttft_ms: int | None = None
    latency_ms: int | None = None
    streamed: list[str] = field(default_factory=list)
    final_answer: str | None = None
    tools: list[dict] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    done: dict | None = None
    failure: BaseException | None = None
    trace_id: str | None = None

    def elapsed_ms(self) -> int:
        return round((time.perf_counter() - self.started) * 1000)

    def observe(self, event: dict) -> dict:
        """Record one event; returns the event to forward (the `done` event gains timing and cost)."""
        kind = event["type"]
        if kind == "text":
            if self.ttft_ms is None:
                self.ttft_ms = self.elapsed_ms()
            self.streamed.append(event["delta"])
        elif kind == "tool_result":
            self.tools.append({"name": event["name"], "input": json.dumps(event.get("input"), default=str)[:300],
                               "ms": event.get("ms"), "is_error": bool(event.get("is_error")),
                               "sources": len(event.get("sources") or []), "summary": event.get("summary")})
        elif kind == "retrieval":
            self.steps.append({"kind": "retrieval", "ms": event.get("ms"), "sources": len(event.get("sources") or [])})
        elif kind == "_step":
            self.steps.append({"kind": "model", **{k: v for k, v in event.items() if k != "type"}})
        elif kind == "error":
            self.errors.append(event["message"])
        elif kind == "notice":
            self.notices.append(event["message"])
        elif kind == "_messages":
            self.final_answer = _last_assistant_text(event["messages"])
        elif kind == "done":
            self.latency_ms = self.elapsed_ms()
            self.done = event
            event = {**event, "latency_ms": self.latency_ms, "ttft_ms": self.ttft_ms, "cost_usd": self.cost()}
        return event

    def model_steps(self) -> list[dict]:
        return [s for s in self.steps if s.get("kind") == "model"]

    def usage(self) -> Counter:
        total: Counter = Counter()
        for step in self.model_steps():
            for key in TOKEN_FIELDS:
                total[key] += (step.get("usage") or {}).get(key, 0)
        return total

    def cost(self) -> float | None:
        steps = self.model_steps()
        costs = [cost_usd(s.get("model") or self.requested_model, s.get("usage") or {}) for s in steps]
        if any(c is None for c in costs):
            return None
        return round(sum(costs), 6)

    def status(self) -> tuple[str, str | None]:
        if self.failure is not None:
            cancelled = isinstance(self.failure, (asyncio.CancelledError, GeneratorExit))
            return ("cancelled" if cancelled else "error"), f"{type(self.failure).__name__}: {self.failure}"[:1000]
        errors = "; ".join(self.errors)[:1000] or None
        if self.done is None:
            return "error", errors or "The answer ended without finishing."
        if self.done.get("stop_reason") == "refusal":
            return "refused", errors
        if self.done.get("stop_reason") == "max_tokens" or any("output limit" in n for n in self.notices):
            return "cut_off", errors
        return "ok", errors

    def models(self) -> list[str]:
        if self.done and self.done.get("models"):
            return list(self.done["models"])
        return [s["model"] for s in self.model_steps() if s.get("model")]


def _last_assistant_text(messages: list[dict]) -> str | None:
    for message in reversed(messages):
        if message["role"] != "assistant":
            continue
        content = message["content"]
        if isinstance(content, str):
            return content
        return "\n".join(b["text"] for b in content if b.get("type") == "text" and b.get("text"))
    return None


async def observe_answer(events: AsyncIterator[dict], *, source: str, repo: dict, question: str, mode: str,
                         settings: Settings, conversation_id: str | None = None) -> AsyncIterator[dict]:
    """Pass `events` through while recording the answer (see the module docstring)."""
    run = AnswerRun(source=source, repo_id=repo["id"], mode=mode, question=question,
                    requested_model=settings.agent_model, conversation_id=conversation_id)
    with tracer.start_as_current_span("answer", attributes={
            "codedoc.source": source, "codedoc.repo_id": repo["id"], "codedoc.repo": repo["name"],
            "codedoc.mode": mode, "gen_ai.request.model": settings.agent_model}) as span:
        run.trace_id = current_trace_id()
        try:
            async for event in events:
                yield run.observe(event)
        except BaseException as exc:
            run.failure = exc
            raise
        finally:
            if run.latency_ms is None:
                run.latency_ms = run.elapsed_ms()
            status, error = run.status()
            span.set_attributes({"codedoc.status": status, "codedoc.tool_calls": len(run.tools),
                                 "codedoc.latency_ms": run.latency_ms, **({"codedoc.cost_usd": run.cost()}
                                                                          if run.cost() is not None else {})})
            if status in ("error", "refused"):
                span.set_status(Status(StatusCode.ERROR, error or status))
            # Shielded: when the client disconnects, the stream is cancelled and every await would be too.
            with anyio.CancelScope(shield=True):
                answer_id = await _save(run, status, error, settings)
            usage = run.usage()
            log.log(logging.WARNING if status == "error" else logging.INFO, "answer.finished", extra={
                "answer_id": answer_id, "source": source, "repo_id": repo["id"], "mode": mode, "status": status,
                "error": error, "model": (run.models() or [settings.agent_model])[-1], "latency_ms": run.latency_ms,
                "ttft_ms": run.ttft_ms, "steps": len(run.model_steps()), "tool_calls": len(run.tools),
                "tool_errors": sum(t["is_error"] for t in run.tools), "cost_usd": run.cost(),
                **{key: usage[key] for key in TOKEN_FIELDS}})


async def _save(run: AnswerRun, status: str, error: str | None, settings: Settings) -> int | None:
    usage = run.usage()
    answer = run.final_answer if run.final_answer is not None else "".join(run.streamed)
    row = {
        "source": run.source, "repo_id": run.repo_id, "conversation_id": run.conversation_id, "mode": run.mode,
        "question": run.question if settings.record_answer_text else None,
        "answer": answer if settings.record_answer_text else None,
        "requested_model": run.requested_model, "models": run.models(), "status": status,
        "stop_reason": (run.done or {}).get("stop_reason"), "error": error,
        "latency_ms": run.latency_ms, "ttft_ms": run.ttft_ms, "steps": len(run.model_steps()),
        "tool_calls": len(run.tools), "tool_errors": sum(t["is_error"] for t in run.tools),
        "input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"],
        "cache_read_tokens": usage["cache_read_input_tokens"],
        "cache_write_tokens": usage["cache_creation_input_tokens"], "cost_usd": run.cost(),
        "tools": json.dumps(run.tools), "step_log": json.dumps(run.steps, default=str), "trace_id": run.trace_id,
    }
    try:
        async with connection() as conn:
            result = await (await conn.execute(
                f"INSERT INTO answer_runs ({', '.join(row)}) VALUES ({', '.join(['%s'] * len(row))}) RETURNING id",
                list(row.values()))).fetchone()
        return result["id"]
    except Exception:  # recording must never break answering
        log.exception("could not record the answer", extra={"repo_id": run.repo_id, "source": run.source})
        return None
