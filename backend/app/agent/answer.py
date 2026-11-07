"""The single entry point for answering a question about a repository.

Both the HTTP API and the evaluation runner call `answer_question`, so the eval exercises exactly the
code that ships. Besides the events documented in `loop.py`, it first yields an internal
{"type": "_system", "system": str} event carrying the system prompt (the eval records it in traces).
"""

from collections.abc import AsyncIterator

import anthropic

from app.agent.loop import ClaudeModel, run_agent
from app.agent.prompts import repo_context, system_prompt
from app.agent.single_shot import RAG_SYSTEM_PROMPT, run_rag
from app.agent.tools import ToolContext, has_graph, tools_for
from app.config import Settings, get_settings
from app.retrieval.embeddings import get_embedder, get_reranker

MODES = ("agent", "rag")


async def answer_question(
    question: str,
    repo: dict,
    *,
    mode: str = "agent",
    history: list[dict] | None = None,
    settings: Settings | None = None,
    client: anthropic.AsyncAnthropic | None = None,
    search_mode: str = "hybrid",
    rerank: bool = True,
    graph_tools: bool | None = None,
) -> AsyncIterator[dict]:
    """`mode="agent"` runs the tool-using agent (the default); `mode="rag"` retrieves once and answers in a
    single call. `search_mode` and `rerank` only apply to `rag` (the agent's search tool is always hybrid).
    `graph_tools` offers the agent the code-graph tools; by default, whenever the repository has a graph."""
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}")
    settings = settings or get_settings()
    ctx = ToolContext(repo=repo, embedder=get_embedder(settings), reranker=get_reranker(settings))
    graph_tools = has_graph(repo) if graph_tools is None else graph_tools
    prompt = system_prompt(graph_tools) if mode == "agent" else RAG_SYSTEM_PROMPT
    system = [{"type": "text", "text": prompt}, {"type": "text", "text": repo_context(repo)}]
    yield {"type": "_system", "system": "\n\n".join(block["text"] for block in system)}

    if mode == "agent":
        model = ClaudeModel(settings, system, client, tools=tools_for(graph_tools))
        async for event in run_agent(question, history or [], ctx, model, settings.agent_max_steps):
            yield event
    else:
        model = ClaudeModel(settings, system, client, tools=None)
        async for event in run_rag(question, ctx, model, search_mode=search_mode, rerank=rerank):
            yield event
