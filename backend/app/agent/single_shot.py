"""Single-shot retrieval-augmented answering: retrieve once, call Claude once, no tools.

This is the classic RAG pipeline. The assistant's default is the agent loop (`loop.run_agent`); this path
exists so the evaluation can compare the two designs, and the API exposes it as a cheaper `rag` mode.
It yields the same event types as `run_agent`, plus one {"type": "retrieval", "sources": [...], "ms": int} event.
"""

import time
from collections import Counter
from collections.abc import AsyncIterator

from app.agent.loop import Model, _add_usage, _as_dict, elapsed_ms, finish_chat_span, start_chat_span
from app.agent.tools import ToolContext, numbered
from app.retrieval.search import SearchHit, hybrid_search

RAG_SYSTEM_PROMPT = """\
You answer questions about a single software repository for developers who are new to it. Each \
question comes with code excerpts retrieved from the repository. They are your only view of the code, \
so every statement about the code must come from them.

- Lead with the direct answer, then the supporting detail. Use Markdown.
- Cite the code behind each claim as [path:START-END] or [path:LINE], using the paths and line numbers \
shown on the excerpts. Put citations right after the sentence they support.
- If the excerpts do not contain the answer, say so plainly and do not guess about code you have not seen.
"""


def format_excerpts(hits: list[SearchHit]) -> str:
    parts = []
    for n, hit in enumerate(hits, 1):
        body = numbered(hit.content.split("\n"), hit.start_line)
        parts.append(f'<excerpt index="{n}" source="{hit.path}:{hit.start_line}-{hit.end_line}">\n{body}\n</excerpt>')
    return "\n\n".join(parts)


async def run_rag(question: str, ctx: ToolContext, model: Model, *, k: int = 8, search_mode: str = "hybrid",
                  rerank: bool = True) -> AsyncIterator[dict]:
    started = time.perf_counter()
    hits = await hybrid_search(ctx.repo_id, question, ctx.embedder, ctx.reranker,
                               repo_embed_model=ctx.repo["stats"].get("embed_model"), k=k,
                               mode=search_mode, rerank=rerank)
    sources = [{"path": h.path, "start_line": h.start_line, "end_line": h.end_line} for h in hits]
    yield {"type": "retrieval", "sources": sources, "ms": elapsed_ms(started)}

    excerpts = format_excerpts(hits) if hits else "(no excerpts matched the question)"
    messages: list[dict] = [{"role": "user", "content": f"<excerpts>\n{excerpts}\n</excerpts>\n\nQuestion: {question}"}]
    usage: Counter = Counter()
    response = None
    started = time.perf_counter()
    first_output: float | None = None
    with start_chat_span(model) as span:
        async for kind, payload in model.stream(messages, allow_tools=False):
            if kind == "text":
                first_output = first_output or time.perf_counter()
                yield {"type": "text", "delta": payload}
            elif kind == "final":
                response = payload
        if response is not None:
            _add_usage(usage, response.usage)
            finish_chat_span(span, response, usage, elapsed_ms(started, first_output) if first_output else None)
    if response is None:
        yield {"type": "error", "message": "The model stream ended without a message."}
        return
    served = [response.model] if getattr(response, "model", None) else []
    yield {"type": "_step", "n": 1, "model": served[0] if served else None, "usage": dict(usage),
           "stop_reason": response.stop_reason, "ms": elapsed_ms(started),
           "ttft_ms": elapsed_ms(started, first_output) if first_output else None}
    done = {"type": "done", "usage": dict(usage), "stop_reason": response.stop_reason, "tool_calls": 0,
            "model": served[0] if served else None, "models": served}
    if response.stop_reason == "refusal":
        yield {"type": "error", "message": "Claude declined this request."}
        yield done
        return
    if response.stop_reason == "max_tokens":
        yield {"type": "notice", "message": "The answer hit the output limit and may be cut off."}
    messages.append({"role": "assistant", "content": [_as_dict(b) for b in response.content]})
    yield {"type": "_messages", "messages": messages}
    yield done
