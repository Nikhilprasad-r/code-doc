"""The agent loop: stream Claude's turn, run the tools it asks for, repeat until it answers.

`run_agent` yields UI events:
  {"type": "text", "delta": str}                      answer text as it streams
  {"type": "tool_start", "name": str}                 Claude started a tool call
  {"type": "tool_result", "name", "input", "summary", "sources", "is_error"}
  {"type": "notice", "message": str}                  non-fatal conditions
  {"type": "error", "message": str}                   the turn ended without an answer
  {"type": "done", "usage": {...}}
plus one internal {"type": "_messages", "messages": [...]} event with the full history to persist.
"""

import asyncio
from collections import Counter
from collections.abc import AsyncIterator
from typing import Any, Protocol

import anthropic

from app.agent.tools import TOOLS, ToolContext, ToolOutput, run_tool
from app.config import Settings

# Models that accept server-side refusal fallbacks (`fallbacks: "default"`).
FALLBACK_MODELS = {"claude-fable-5-1", "claude-fable-5", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"

STEP_LIMIT_NOTE = ("You have reached the tool-call limit for this question. Answer now from the evidence "
                   "gathered so far, with citations, and say what you could not verify.")


class Model(Protocol):
    def stream(self, messages: list[dict], *, allow_tools: bool) -> AsyncIterator[tuple[str, Any]]: ...


class ClaudeModel:
    """Streams one assistant turn. Yields ("text", str), ("tool_start", name), then ("final", message)."""

    def __init__(self, settings: Settings, system: list[dict], client: anthropic.AsyncAnthropic | None = None):
        self.settings = settings
        self.system = system
        self.client = client or anthropic.AsyncAnthropic()

    async def stream(self, messages: list[dict], *, allow_tools: bool) -> AsyncIterator[tuple[str, Any]]:
        params: dict[str, Any] = {
            "model": self.settings.agent_model,
            "max_tokens": 64000,
            "system": self.system,
            "tools": TOOLS,
            "messages": messages,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.settings.agent_effort},
            # Tools and system prompt are stable, so each step re-reads the growing prefix from cache.
            "cache_control": {"type": "ephemeral"},
        }
        if not allow_tools:
            params["tool_choice"] = {"type": "none"}
        if self.settings.agent_server_fallback and self.settings.agent_model in FALLBACK_MODELS:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        async with self.client.beta.messages.stream(**params) as stream:
            async for event in stream:
                if event.type == "text":
                    yield "text", event.text
                elif event.type == "content_block_start" and event.content_block.type == "tool_use":
                    yield "tool_start", event.content_block.name
            yield "final", await stream.get_final_message()


def _as_dict(block: Any) -> dict:
    return block if isinstance(block, dict) else block.to_dict(mode="json")


def echo_content(blocks: list[dict]) -> list[dict]:
    """Content to send back after a turn. After a mid-output fallback, blocks before the last
    `fallback` marker other than text belong to the declined attempt and must not be echoed."""
    last = max((i for i, b in enumerate(blocks) if b.get("type") == "fallback"), default=None)
    if last is None:
        return blocks
    return [b for b in blocks[:last] if b.get("type") == "text"] + blocks[last + 1:]


def _add_usage(total: Counter, usage: Any) -> None:
    if usage is None:
        return
    for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
        total[key] += value or 0


def _summary(output: ToolOutput) -> str:
    first = output.text.split("\n", 1)[0]
    return first[:160]


async def run_agent(question: str, history: list[dict], ctx: ToolContext, model: Model,
                    max_steps: int) -> AsyncIterator[dict]:
    messages: list[dict] = [*history, {"role": "user", "content": question}]
    usage: Counter = Counter()
    allow_tools = True
    json_retries = 0
    steps = 0

    while True:
        response = None
        try:
            async for kind, payload in model.stream(messages, allow_tools=allow_tools):
                if kind == "text":
                    yield {"type": "text", "delta": payload}
                elif kind == "tool_start":
                    yield {"type": "tool_start", "name": payload}
                elif kind == "final":
                    response = payload
        except ValueError:
            # The SDK could not parse a streamed tool input at all; re-issue the turn (bounded).
            json_retries += 1
            if json_retries > 2:
                yield {"type": "error", "message": "Claude produced unreadable tool input three times."}
                return
            yield {"type": "notice", "message": "Retrying a step after malformed tool input."}
            continue
        json_retries = 0
        if response is None:
            yield {"type": "error", "message": "The model stream ended without a message."}
            return
        _add_usage(usage, response.usage)

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            yield {"type": "error", "message": f"Claude declined this request{f' ({category})' if category else ''}."}
            return

        content = echo_content([_as_dict(b) for b in response.content])
        messages.append({"role": "assistant", "content": content})
        if response.stop_reason == "pause_turn":
            continue

        tool_uses = [b for b in content if b.get("type") == "tool_use"]
        if not tool_uses:
            if response.stop_reason == "max_tokens":
                yield {"type": "notice", "message": "The answer hit the output limit and may be cut off."}
            break
        if response.stop_reason == "max_tokens":
            yield {"type": "error", "message": "A tool call was cut off at the output limit."}
            return

        outputs = await asyncio.gather(*(run_tool(ctx, b["name"], b.get("input")) for b in tool_uses),
                                       return_exceptions=True)
        results: list[dict] = []
        for block, output in zip(tool_uses, outputs):
            if isinstance(output, BaseException):
                output = ToolOutput(f"Tool {block['name']} failed: {output}", is_error=True)
            yield {"type": "tool_result", "id": block["id"], "name": block["name"], "input": block.get("input"),
                   "summary": _summary(output), "sources": output.sources, "is_error": output.is_error}
            result = {"type": "tool_result", "tool_use_id": block["id"], "content": output.clipped()}
            if output.is_error:
                result["is_error"] = True
            results.append(result)

        steps += 1
        if steps >= max_steps:
            results.append({"type": "text", "text": STEP_LIMIT_NOTE})
            allow_tools = False
        messages.append({"role": "user", "content": results})

    yield {"type": "_messages", "messages": messages}
    yield {"type": "done", "usage": dict(usage)}
