# MCP server: use it from Claude Code, Cursor, or any MCP client

The assistant is also an MCP server, so coding agents can query indexed repositories directly. Its
code tools are the agent's own, so a client sees exactly what the built-in agent sees.

| Tool | What it does |
|---|---|
| `list_repositories` | Indexed repositories with status, commit, and size |
| `ask_codebase` | Runs the full agent and returns a cited answer (uses the server's Anthropic key; reports progress per step) |
| `repo_overview`, `search_code`, `read_file`, `grep`, `find_symbol` | Explore the code |
| `list_endpoints`, `list_dependencies`, `find_imports` | The structured tables |
| `architecture`, `related`, `impact` | The code graph: modules, a symbol's links, what depends on it |
| `index_repository` | Index or re-index a git URL (or local folder, when allowed) |

Every code tool takes `repo` (a name like `spring-petclinic`, or an id). It can be left out when only
one repository is indexed or `CODEDOC_REPO` is set.

**Over HTTP**, with the Docker stack running:

```bash
claude mcp add --transport http codedoc http://localhost:8000/mcp
```

**Over stdio**, without Docker for the server itself (the database still has to be running):

```bash
claude mcp add codedoc -- uv --directory /path/to/code-doc/backend run python -m app.mcp_server
```

For Cursor or other clients that read an `mcpServers` JSON config:

```json
{
  "mcpServers": {
    "codedoc": { "url": "http://localhost:8000/mcp" }
  }
}
```

The HTTP endpoint has no authentication, like the rest of the API. It only accepts requests whose
`Host` and `Origin` are localhost (DNS-rebinding protection); widen that with `MCP_ALLOWED_HOSTS` and
`MCP_ALLOWED_ORIGINS` (JSON lists) only behind your own auth. `READ_ONLY=true` disables
`index_repository`, and `ALLOW_LOCAL_PATHS=false` limits it to git URLs.
