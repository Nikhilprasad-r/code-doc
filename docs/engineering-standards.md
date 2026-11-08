# Engineering standards

**Followed:**
- **Clear module boundaries:** ingest, retrieval, agent, observability, evals. Docstrings explain the
  why, not the what.
- **Validated inputs:** typed Python with pydantic validation at the edges (API bodies, tool inputs,
  settings).
- **Tests close to real use:** integration tests run against a real Postgres. A scripted model stands
  in for Claude, so the agent loop, MCP server and answer log are tested without API calls.
- **An eval with safeguards:** self-checks and an approval gate, so quality claims can be measured
  and reproduced.
- **Reproducible builds:**
  - Lockfiles for Python (`uv.lock`) and Node (`pnpm-lock.yaml`).
  - Exact pins for graphify and OpenTelemetry, to releases at least two weeks old.
  - Re-tested after the graphify version changed.
- **One-command Docker setup;** configuration through environment variables only; no secrets in the
  repository.
- **Observable by default:** structured logs, a record of every answer, opt-in tracing.
- **Security basics:** safe zip extraction, read-only tools, a minimal environment for subprocesses,
  DNS-rebinding protection on MCP, graphify's HTML view served in a sandbox, and no prompt or answer
  text in traces.
- **Frontend:** strict TypeScript and ESLint.

**Skipped, for time:**
- CI pipeline.
- Python linter, formatter and type checker in the toolchain.
- Frontend tests.
- Database migrations.
- Authentication.
- Load tests.
- Docker images pinned by digest.
- An accessibility audit of the UI.

---

[README](../README.md#documentation) · Previous: [Productionizing and scaling](production.md) · Next: [How I used AI tools](ai-tools.md)
