# Key technical decisions

| Decision | Why | Trade-off |
|---|---|---|
| Agent with tools instead of one retrieve-then-answer call | Code questions are multi-hop: find the endpoint, read the handler, follow the call. Tool-using agents beat single-shot RAG on repository QA ([SWE-QA](https://arxiv.org/abs/2509.14635)). | More latency and tokens per answer, bounded by the step limit. The eval measures whether it pays off. |
| Structured tables for endpoints and dependencies | "List all endpoints" needs a complete answer, which top-k retrieval can't guarantee. | Pattern-based extraction misses unusual routing styles; `grep` is the fallback. |
| Keyword + vector search, reranked | Embeddings blur exact identifiers; keyword search finds them but misses paraphrases. | Two queries per search plus a rerank call. |
| One Postgres for every index | One service, transactional writes, joins across indexes. HNSW is fine into the millions of chunks. | Beyond that, vectors move to a dedicated store. |
| Index pinned to a commit | Citations always point at the exact code that was read. The eval's repositories are pinned the same way. | Re-indexing is needed to follow a branch. |
| Embedding cache keyed by content hash, chunk headers without line numbers | Re-indexing re-embeds only changed code. | A cache table to keep. |
| Code graph from graphify, as a separate process, pinned to an exact release | Multi-language extraction and module detection in 1–2 s per demo repo, no LLM calls. The separate process contains crashes and keeps API keys out. | A third-party format to track; covered by tests on its output. |
| No orchestration framework | Full control of streaming events, caching, fallback, and eval parity (see [Orchestration framework](rag-and-llm.md#orchestration-framework)). | More code owned: the loop, validation and retries. |
| Same tools over MCP | Coding agents (Claude Code, Cursor) can use the index, and the tools get a second set of callers. | One more endpoint to secure. |
| Observability built in (answer log, JSON logs, OpenTelemetry) | Cost and latency per answer are product questions, not just ops ones. | More code in the request path; tracing is off unless configured. |
| Haiku for the demo, model configurable | Haiku is 40× cheaper per token than Opus, which keeps exploring and recording the demo cheap. | Weaker multi-step reasoning and citation discipline; the eval quantifies this. |
| Local embeddings on a Mac (Qwen3-Embedding-0.6B with MLX) | Semantic search without a second key or per-token cost. MLX can't use the GPU from inside Docker, so it runs as a server on the Mac. | Apple Silicon only; a general-purpose model; no reranker. Voyage stays the production choice. |
| Offline embedding fallback | The stack and tests run without keys. | Not semantic; development only. |

---

[README](../README.md#documentation) · Previous: [RAG and LLM approach](rag-and-llm.md) · Next: [Productionizing and scaling](production.md)
