"""The three designs under comparison, plus two harness checks.

Every real design goes through `app.agent.answer.answer_question`, the same entry point the HTTP API
uses; designs only differ in the index they read (chunking) and the answering mode.
"""

import json
import time
from dataclasses import dataclass, field

import anthropic

from app.agent.answer import answer_question
from app.config import Settings


@dataclass(frozen=True)
class Design:
    name: str
    variant: str          # report directory: baseline, v1, v2, ...
    description: str
    flow: str = "code-qa"  # report folder under .claude/hillclimb/
    chunking: str = "ast"
    mode: str = "agent"   # agent | rag | oracle | null
    search_mode: str = "hybrid"
    rerank: bool = True

    @property
    def calls_model(self) -> bool:
        return self.mode in {"agent", "rag"}


DESIGNS = {d.name: d for d in [
    Design("naive-rag", "baseline", "(a) Fixed 40-line chunks, vector search only, one model call",
           chunking="lines", mode="rag", search_mode="vector", rerank=False),
    Design("hybrid-rag", "v1", "(b) Syntax-aware chunks, keyword + vector search, reranked, one model call",
           mode="rag", search_mode="hybrid", rerank=True),
    Design("agent", "v2", "(c) Agent with 8 tools over syntax-aware chunks, symbols, endpoints, dependencies",
           mode="agent"),
    # Harness checks: no model calls. `oracle` answers with the gold facts and citations (must score ~1),
    # `null` answers with an empty string (must score ~0).
    Design("null", "baseline", "Harness check: empty answer", flow="code-qa-checks", mode="null"),
    Design("oracle", "v1", "Harness check: answer = gold facts + gold citations", flow="code-qa-checks",
           mode="oracle"),
]}


@dataclass
class Outcome:
    answer: str
    trace: list[dict]
    sources: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    models: list[str] = field(default_factory=list)
    stop_reason: str | None = None
    tool_calls: int = 0
    latency_s: float = 0.0
    refused: bool = False
    errors: list[str] = field(default_factory=list)


def _messages_to_turns(system: str, messages: list[dict]) -> list[dict]:
    """Anthropic message history -> report trace turns (system/user/assistant/tool_call/tool_result)."""
    turns: list[dict] = [{"role": "system", "content": system}]
    tool_names: dict[str, str] = {}
    pending_thinking: list[str] = []
    for message in messages:
        content = message["content"]
        if isinstance(content, str):
            turns.append({"role": message["role"], "content": content})
            continue
        for block in content:
            kind = block.get("type")
            if kind == "thinking" and block.get("thinking"):
                pending_thinking.append(block["thinking"])
            elif kind == "text" and block.get("text"):
                turn = {"role": message["role"], "content": block["text"]}
                if pending_thinking and message["role"] == "assistant":
                    turn["thinking"] = "\n\n".join(pending_thinking)
                    pending_thinking = []
                turns.append(turn)
            elif kind == "tool_use":
                tool_names[block["id"]] = block["name"]
                turn = {"role": "tool_call", "name": block["name"],
                        "content": json.dumps(block.get("input"), indent=2)}
                if pending_thinking:
                    turn["thinking"] = "\n\n".join(pending_thinking)
                    pending_thinking = []
                turns.append(turn)
            elif kind == "tool_result":
                result = block.get("content")
                if isinstance(result, list):
                    result = "\n".join(part.get("text", "") for part in result if isinstance(part, dict))
                turns.append({"role": "tool_result", "name": tool_names.get(block.get("tool_use_id"), ""),
                              "content": str(result)})
    return turns


def _final_answer(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message["role"] == "assistant":
            content = message["content"]
            if isinstance(content, str):
                return content
            return "\n".join(b["text"] for b in content if b.get("type") == "text" and b.get("text"))
    return ""


async def run_design(design: Design, case: dict, repo: dict, settings: Settings,
                     client: anthropic.AsyncAnthropic) -> Outcome:
    question = case["question"]
    if design.mode == "oracle":
        cites = " ".join(f"[{g['path']}:{g['start']}-{g['end']}]" for g in case["gold"])
        answer = "\n".join(f"- {fact}" for fact in case["key_facts"]) + (f"\n\nSources: {cites}" if cites else "")
        sources = [{"path": g["path"], "start_line": g["start"], "end_line": g["end"]} for g in case["gold"]]
        return Outcome(answer, [{"role": "user", "content": question}, {"role": "assistant", "content": answer}],
                       sources=sources, stop_reason="end_turn")
    if design.mode == "null":
        return Outcome("", [{"role": "user", "content": question}, {"role": "assistant", "content": ""}],
                       stop_reason="end_turn")

    system, messages, streamed, sources, errors = "", [], [], [], []
    done: dict = {}
    started = time.monotonic()
    async for event in answer_question(question, repo, mode=design.mode, settings=settings, client=client,
                                       search_mode=design.search_mode, rerank=design.rerank):
        kind = event["type"]
        if kind == "_system":
            system = event["system"]
        elif kind == "_messages":
            messages = event["messages"]
        elif kind == "text":
            streamed.append(event["delta"])
        elif kind in {"retrieval", "tool_result"}:
            sources.extend(event.get("sources") or [])
        elif kind == "error":
            errors.append(event["message"])
        elif kind == "done":
            done = event
    latency = time.monotonic() - started

    if messages:
        trace = _messages_to_turns(system, messages)
        answer = _final_answer(messages)
    else:  # e.g. a refusal: no history is returned
        answer = "".join(streamed)
        trace = [{"role": "system", "content": system}, {"role": "user", "content": question},
                 {"role": "assistant", "content": answer or f"[no answer: {'; '.join(errors)}]"}]
    return Outcome(
        answer=answer, trace=trace, sources=sources, usage=done.get("usage", {}), models=done.get("models", []),
        stop_reason=done.get("stop_reason"), tool_calls=done.get("tool_calls", 0), latency_s=round(latency, 2),
        refused=done.get("stop_reason") == "refusal", errors=errors,
    )
