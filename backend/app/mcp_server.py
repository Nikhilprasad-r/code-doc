"""MCP server: the assistant's code-intelligence tools for any MCP client (Claude Code, Cursor, ...).

Local clients start it over stdio:

    uv --directory backend run python -m app.mcp_server

The API also serves it over streamable HTTP at http://localhost:8000/mcp (see app/main.py).

The code tools are the agent's own (`app.agent.tools`), so an MCP client sees exactly what the agent
sees. `ask_codebase` runs the whole agent and returns its cited answer.
"""

import asyncio
import logging
import os
from typing import Annotated

import anthropic
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from app.agent.answer import answer_question
from app.agent.tools import TOOLS, ToolContext, run_tool
from app.config import get_settings
from app.db import connection
from app.ingest.pipeline import index_repo
from app.repos import RepoError, create_repo, list_repos, resolve_repo
from app.retrieval.embeddings import get_embedder, get_reranker

log = logging.getLogger(__name__)

INSTRUCTIONS = """\
Code Docs Assistant: questions about indexed code repositories, answered from the code itself.

- Call list_repositories to see what is indexed; pass a repository name or id as `repo` to the other \
tools (it can be left out when only one repository is indexed).
- For a complete, cited answer to a broad question, use ask_codebase: it runs the assistant's own agent.
- To investigate yourself: repo_overview for layout, search_code for behaviour, find_symbol for \
definitions, grep for exact strings, read_file to read code. list_endpoints and list_dependencies are \
complete tables extracted from the code and manifests; prefer them for API and dependency questions.
- The code graph: architecture for modules and the most connected symbols, related for what a symbol \
uses and what uses it, impact for everything a change to it could affect. Calls are resolved statically \
and can be missing, so confirm call sites with grep.
- Results carry repository-relative paths and line numbers; cite them as path:start-end.
"""

server = MCPServer(name="codedoc", title="Code Docs Assistant", instructions=INSTRUCTIONS, version="0.1.0")

DESCRIPTIONS = {tool["name"]: tool["description"] for tool in TOOLS}
READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)

Repo = Annotated[
    str | int | None,
    Field(description="Repository name ('spring-projects/spring-petclinic', or just 'spring-petclinic') or id. "
                      "Optional when only one repository is indexed or CODEDOC_REPO is set."),
]


async def _tool_context(repo: str | int | None) -> ToolContext:
    settings = get_settings()
    try:
        row = await resolve_repo(repo, default=os.environ.get("CODEDOC_REPO"))
    except RepoError as exc:
        raise ToolError(str(exc)) from exc
    return ToolContext(repo=row, embedder=get_embedder(settings), reranker=get_reranker(settings))


def _header(row: dict) -> str:
    return f"[{row['name']} @ {(row.get('commit_sha') or 'unknown commit')[:10]}]\n"


async def _run(tool: str, repo: str | int | None, /, **args: object) -> str:
    ctx = await _tool_context(repo)
    output = await run_tool(ctx, tool, {k: v for k, v in args.items() if v is not None})
    if output.is_error:
        raise ToolError(output.text)
    return _header(ctx.repo) + output.clipped()


# --- repositories ------------------------------------------------------------------------------

@server.tool(title="List repositories", annotations=READ_ONLY)
async def list_repositories() -> str:
    """List the indexed repositories with their status, commit, size and languages."""
    rows = await list_repos()
    if not rows:
        return "No repositories are indexed yet. Use index_repository to add one."
    lines = []
    for r in rows:
        stats = r["stats"] or {}
        languages = ", ".join(list(stats.get("languages", {}))[:5])
        detail = (f"{stats.get('files', '?')} files, {stats.get('endpoints', 0)} endpoints, "
                  f"{stats.get('dependencies', 0)} dependencies; {languages}") if stats else r["status"]
        lines.append(f"- {r['name']} (id {r['id']}, {r['status']}, commit {(r['commit_sha'] or '?')[:10]}): {detail}")
    return "\n".join(lines)


@server.tool(title="Index a repository",
             annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True,
                                         open_world_hint=True))
async def index_repository(
    source: Annotated[str, Field(description="https git URL, or a local folder path when the server allows it")],
    ctx: Context,
    ref: Annotated[str | None, Field(description="Branch, tag or 40-character commit to index")] = None,
) -> str:
    """Index (or re-index) a repository so the other tools can answer questions about it.
    Takes seconds for small repositories and a few minutes for large ones."""
    settings = get_settings()
    existing = [r for r in await list_repos() if r["source"].rstrip("/") == source.strip().rstrip("/")]
    try:
        row = existing[0] if existing else await create_repo(source, ref, settings)
    except RepoError as exc:
        raise ToolError(str(exc)) from exc
    if existing and ref and ref != row["ref"]:
        async with connection() as conn:
            await conn.execute("UPDATE repos SET ref = %s WHERE id = %s", [ref, row["id"]])

    task = asyncio.create_task(index_repo(row["id"], settings))
    tick = 0
    while not task.done():
        await asyncio.sleep(1)
        tick += 1
        async with connection() as conn:
            progress = (await (await conn.execute(
                "SELECT progress FROM repos WHERE id = %s", [row["id"]])).fetchone())["progress"]
        phase = progress.get("phase", "working")
        detail = f" {progress['done']}/{progress['total']} chunks" if progress.get("total") else ""
        await ctx.report_progress(tick, None, f"{phase}{detail}")
    try:
        stats = task.result()
    except Exception as exc:
        raise ToolError(f"Indexing {row['name']} failed: {exc}") from exc
    return (f"Indexed {row['name']} (id {row['id']}) in {stats['seconds']}s: {stats['files']} files, "
            f"{stats['chunks']} chunks, {stats['symbols']} symbols, {stats['endpoints']} endpoints, "
            f"{stats['dependencies']} dependencies. Embeddings: {stats['embed_model']}.")


# --- the agent's code tools --------------------------------------------------------------------

@server.tool(title="Repository overview", description=DESCRIPTIONS["repo_overview"], annotations=READ_ONLY)
async def repo_overview(repo: Repo = None) -> str:
    return await _run("repo_overview", repo)


@server.tool(title="Search code", description=DESCRIPTIONS["search_code"], annotations=READ_ONLY)
async def search_code(
    query: Annotated[str, Field(description="What to look for: a concept, behaviour, or identifier")],
    repo: Repo = None,
    path_prefix: Annotated[str | None, Field(description="Only search under this directory, e.g. 'src/api/'")] = None,
    k: Annotated[int, Field(ge=1, le=15, description="Number of results")] = 8,
) -> str:
    return await _run("search_code", repo, query=query, path_prefix=path_prefix, k=k)


@server.tool(title="Read file", description=DESCRIPTIONS["read_file"], annotations=READ_ONLY)
async def read_file(
    path: Annotated[str, Field(description="Repository-relative file path")],
    repo: Repo = None,
    start_line: Annotated[int | None, Field(ge=1, description="First line (1-based)")] = None,
    end_line: Annotated[int | None, Field(ge=1, description="Last line (inclusive)")] = None,
) -> str:
    return await _run("read_file", repo, path=path, start_line=start_line, end_line=end_line)


@server.tool(title="Grep", description=DESCRIPTIONS["grep"], annotations=READ_ONLY)
async def grep(
    pattern: Annotated[str, Field(description="Python regular expression")],
    repo: Repo = None,
    path_glob: Annotated[str | None, Field(description="Only files matching this glob, e.g. '*.py'")] = None,
    case_sensitive: bool = False,
    max_results: Annotated[int, Field(ge=1, le=200)] = 50,
) -> str:
    return await _run("grep", repo, pattern=pattern, path_glob=path_glob, case_sensitive=case_sensitive,
                      max_results=max_results)


@server.tool(title="Find symbol", description=DESCRIPTIONS["find_symbol"], annotations=READ_ONLY)
async def find_symbol(
    name: Annotated[str, Field(description="Function, class, method or type name; 'Cls.method' works")],
    repo: Repo = None,
    kind: Annotated[str | None, Field(description="function, method, class, interface, type or enum")] = None,
) -> str:
    return await _run("find_symbol", repo, name=name, kind=kind)


@server.tool(title="List endpoints", description=DESCRIPTIONS["list_endpoints"], annotations=READ_ONLY)
async def list_endpoints(
    repo: Repo = None,
    method: Annotated[str | None, Field(description="HTTP method filter, e.g. GET")] = None,
    route_contains: Annotated[str | None, Field(description="Substring of the route, e.g. '/users'")] = None,
) -> str:
    return await _run("list_endpoints", repo, method=method, route_contains=route_contains)


@server.tool(title="List dependencies", description=DESCRIPTIONS["list_dependencies"], annotations=READ_ONLY)
async def list_dependencies(
    repo: Repo = None,
    name_contains: Annotated[str | None, Field(description="Substring of the package name")] = None,
    ecosystem: Annotated[str | None, Field(description="pypi, npm, maven, go or cargo")] = None,
) -> str:
    return await _run("list_dependencies", repo, name_contains=name_contains, ecosystem=ecosystem)


@server.tool(title="Find imports", description=DESCRIPTIONS["find_imports"], annotations=READ_ONLY)
async def find_imports(
    module_contains: Annotated[str, Field(description="Substring of the imported module, e.g. 'redis'")],
    repo: Repo = None,
) -> str:
    return await _run("find_imports", repo, module_contains=module_contains)


# --- the code graph ----------------------------------------------------------------------------

@server.tool(title="Architecture", description=DESCRIPTIONS["architecture"], annotations=READ_ONLY)
async def architecture(
    repo: Repo = None,
    module: Annotated[int | None, Field(ge=0, description="A module number from an earlier call, to list its "
                                                          "members and links")] = None,
) -> str:
    return await _run("architecture", repo, module=module)


Symbol = Annotated[str, Field(description="Class, function, method ('Cls.method') or file path")]
SymbolPath = Annotated[str | None, Field(description="Part of the file path, to choose between symbols that "
                                                     "share a name")]


@server.tool(title="Related code", description=DESCRIPTIONS["related"], annotations=READ_ONLY)
async def related(symbol: Symbol, repo: Repo = None, path: SymbolPath = None) -> str:
    return await _run("related", repo, symbol=symbol, path=path)


@server.tool(title="Impact of a change", description=DESCRIPTIONS["impact"], annotations=READ_ONLY)
async def impact(
    symbol: Symbol,
    repo: Repo = None,
    path: SymbolPath = None,
    depth: Annotated[int, Field(ge=1, le=4, description="How many steps of dependants to follow")] = 2,
) -> str:
    return await _run("impact", repo, symbol=symbol, path=path, depth=depth)


# --- the full agent ----------------------------------------------------------------------------

def _final_answer(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message["role"] == "assistant" and isinstance(message["content"], list):
            return "\n".join(b["text"] for b in message["content"] if b.get("type") == "text" and b.get("text"))
    return ""


@server.tool(title="Ask the codebase",
             annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=False, open_world_hint=True))
async def ask_codebase(
    question: Annotated[str, Field(description="A question about the code, in plain language")],
    ctx: Context,
    repo: Repo = None,
) -> str:
    """Answer a question with the assistant's own agent: it searches and reads the repository, then returns
    an answer citing path:start-end for each claim. Best for broad questions ("how does checkout work?").
    Takes 10-60 seconds and uses the server's Anthropic API key."""
    try:
        row = await resolve_repo(repo, default=os.environ.get("CODEDOC_REPO"))
    except RepoError as exc:
        raise ToolError(str(exc)) from exc
    messages: list[dict] = []
    streamed: list[str] = []
    errors: list[str] = []
    steps = 0
    try:
        async for event in answer_question(question, row, mode="agent"):
            kind = event["type"]
            if kind == "tool_result":
                steps += 1
                await ctx.report_progress(steps, None, f"{event['name']}: {event['summary']}")
            elif kind == "text":
                streamed.append(event["delta"])
            elif kind == "error":
                errors.append(event["message"])
            elif kind == "_messages":
                messages = event["messages"]
    except anthropic.AuthenticationError as exc:
        raise ToolError("The server's Anthropic API key is missing or invalid (ANTHROPIC_API_KEY).") from exc
    except anthropic.APIError as exc:
        raise ToolError(f"Anthropic API error: {exc}") from exc
    except TypeError as exc:  # raised by the SDK when no credentials are configured at all
        if "authentication" not in str(exc).lower():
            raise
        raise ToolError("No Anthropic credentials on the server: set ANTHROPIC_API_KEY in .env.") from exc
    answer = _final_answer(messages) or "".join(streamed)
    if not answer:
        raise ToolError("; ".join(errors) or "The agent returned no answer.")
    return f"{answer}\n\n---\n{_header(row).strip()} answered after {steps} tool calls."


def main() -> None:
    logging.basicConfig(level=logging.WARNING)  # stderr: stdout belongs to the MCP protocol
    server.run("stdio")


if __name__ == "__main__":
    main()
