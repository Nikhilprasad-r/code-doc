# Development and configuration

Run the stack's dependencies in Docker and the apps locally:

```bash
docker compose up -d db redis
```

```bash
cd backend && uv run uvicorn app.main:app --reload
```

```bash
cd backend && uv run arq app.worker.WorkerSettings
```

```bash
cd frontend && pnpm install && pnpm dev
```

Without Redis the API indexes in-process, so the worker is optional locally.

Tests (unit tests always; integration tests when Postgres is up):

```bash
cd backend && uv run pytest
```

## Layout

```
backend/app/
  ingest/      source handling, chunker, symbols, endpoints, dependencies, code graph, pipeline
  retrieval/   tokenizer, embeddings + reranker, hybrid search
  agent/       tools (+ graph tools), system prompt, streaming agent loop, single-call RAG mode
  observability/  JSON logs + request IDs, answer log, /stats queries, OpenTelemetry tracing
  pricing.py   $/MTok per model (answers and evals)
  main.py      HTTP API (+ MCP at /mcp)   mcp_server.py  MCP server   worker.py  arq worker
backend/tests/ unit + integration tests, multi-framework fixture repo
frontend/      Next.js UI (repo list, chat, code viewer, endpoints/dependencies/usage tabs)
```

## Configuration

Set in `.env` (see `.env.example`):

| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | – | Required for chat |
| `AGENT_MODEL` | `claude-opus-5-5` | `claude-sonnet-5-5` is cheaper and faster |
| `AGENT_EFFORT` | `medium` | `low` … `max` |
| `VOYAGE_API_KEY` | – | Enables semantic embeddings and reranking |
| `EMBED_MODEL` / `EMBED_DIM` | `voyage-code-3` / `1024` | Changing the dimension needs a fresh database |
| `RERANK_MODEL` | `rerank-2.5` | |
| `ALLOW_LOCAL_PATHS` | `true` | Set `false` on any shared deployment |
| `READ_ONLY` | `false` | Public demo mode: no adding, re-indexing or deleting repositories |
| `GRAPH_ENABLED` | `true` | Build the code graph while indexing |
| `GRAPH_TIMEOUT_S` | `600` | Give up on the graph (and keep the rest of the index) after this long |
| `LOG_FORMAT` / `LOG_LEVEL` | `json` / `INFO` | `text` for readable local logs |
| `RECORD_ANSWER_TEXT` | `true` | Store questions and answers in `answer_runs` (metrics are recorded either way) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | – | Turns tracing on, e.g. `http://localhost:4318`; set by the observability override |
| `OTEL_SERVICE_NAME` | `codedoc-api` / `-worker` / `-mcp` | Service name in the trace viewer |
