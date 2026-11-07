"""Tools the agent uses to look at one indexed repository.

Each tool returns text for the model plus the source ranges it showed, so the UI can list what the
answer was based on. Inputs are validated with pydantic before anything runs: with eager input
streaming the API no longer validates tool arguments itself.
"""

import fnmatch
import re
from collections import Counter, defaultdict
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.agent import graph_tools
from app.agent.toolbase import MAX_OUTPUT_CHARS, Args, ToolContext, ToolOutput
from app.db import connection
from app.retrieval.search import hybrid_search

__all__ = ["GRAPH_TOOL_NAMES", "MAX_OUTPUT_CHARS", "TOOLS", "ToolContext", "ToolOutput", "has_graph",
           "numbered", "run_tool", "tools_for"]

MAX_READ_LINES = 300


class SearchCodeArgs(Args):
    query: str = Field(description="What to look for: a concept, behaviour, or identifier.", min_length=1)
    path_prefix: str | None = Field(None, description="Only search under this directory, e.g. 'src/api/'.")
    k: int = Field(8, ge=1, le=15, description="Number of results.")


class ReadFileArgs(Args):
    path: str = Field(description="Repository-relative file path, as shown by other tools.")
    start_line: int | None = Field(None, ge=1, description="First line to show (1-based).")
    end_line: int | None = Field(None, ge=1, description="Last line to show (inclusive).")


class GrepArgs(Args):
    pattern: str = Field(description="Python regular expression.", min_length=1, max_length=200)
    path_glob: str | None = Field(None, description="Only files matching this glob, e.g. '*.py' or 'src/**'.")
    case_sensitive: bool = False
    max_results: int = Field(50, ge=1, le=200)


class FindSymbolArgs(Args):
    name: str = Field(description="Function, class, method or type name (qualified names like 'Cls.method' work).",
                      min_length=1)
    kind: str | None = Field(None, description="Optional filter: function, method, class, interface, type, enum.")


class ListEndpointsArgs(Args):
    method: str | None = Field(None, description="HTTP method filter, e.g. GET.")
    route_contains: str | None = Field(None, description="Substring of the route, e.g. '/users'.")


class ListDependenciesArgs(Args):
    name_contains: str | None = Field(None, description="Substring of the package name.")
    ecosystem: str | None = Field(None, description="pypi, npm, maven, go or cargo.")


class FindImportsArgs(Args):
    module_contains: str = Field(description="Substring of the imported module, e.g. 'redis' or 'auth'.",
                                 min_length=1)


class RepoOverviewArgs(Args):
    pass


def _schema(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)
        if "anyOf" in prop:  # `str | None` -> plain optional string for a simpler schema
            types = [t for t in prop.pop("anyOf") if t.get("type") != "null"]
            prop.update(types[0])
    return schema


_DEFINITIONS: list[tuple[str, str, type[BaseModel]]] = [
    ("repo_overview",
     "Summary of the repository: languages, size, top-level layout, README opening, manifests and "
     "endpoint counts. Good first call for broad or architectural questions.",
     RepoOverviewArgs),
    ("search_code",
     "Hybrid keyword + semantic search over code and docs. Returns the best-matching chunks with file "
     "paths and line ranges. Use for 'where/how is X done' questions.",
     SearchCodeArgs),
    ("read_file",
     f"Read a file (or a line range of it) with line numbers. At most {MAX_READ_LINES} lines per call.",
     ReadFileArgs),
    ("grep",
     "Regex search across all indexed files, returning matching lines. Use for exact identifiers, "
     "string literals, config keys, and finding every call site of a function.",
     GrepArgs),
    ("find_symbol",
     "Look up where functions, classes, methods or types are defined, with signatures and line ranges.",
     FindSymbolArgs),
    ("list_endpoints",
     "Every HTTP endpoint found by static extraction (FastAPI, Flask, Express, NestJS, Spring, Go). "
     "Routes are relative to their router; MOUNT rows show where routers are included with a prefix.",
     ListEndpointsArgs),
    ("list_dependencies",
     "Declared package dependencies from manifests (pyproject, requirements, package.json, pom.xml, "
     "build.gradle, go.mod, Cargo.toml) with versions and scopes.",
     ListDependenciesArgs),
    ("find_imports",
     "Which files import a module. Use to see where a library or internal module is used.",
     FindImportsArgs),
]

_DEFINITIONS += graph_tools.DEFINITIONS
GRAPH_TOOL_NAMES = frozenset(name for name, _, _ in graph_tools.DEFINITIONS)

ARG_MODELS = {name: model for name, _, model in _DEFINITIONS}

TOOLS: list[dict[str, Any]] = [
    {"name": name, "description": description, "input_schema": _schema(model), "eager_input_streaming": True}
    for name, description, model in _DEFINITIONS
]


def tools_for(graph: bool) -> list[dict[str, Any]]:
    """The tool list to offer: the graph tools only when the repository has a code graph."""
    return TOOLS if graph else [t for t in TOOLS if t["name"] not in GRAPH_TOOL_NAMES]


def has_graph(repo: dict) -> bool:
    return bool(((repo.get("stats") or {}).get("graph") or {}).get("nodes"))


def numbered(lines: list[str], first_line: int) -> str:
    width = len(str(first_line + len(lines)))
    return "\n".join(f"{first_line + i:>{width}}  {line}" for i, line in enumerate(lines))


async def _search_code(ctx: ToolContext, args: SearchCodeArgs) -> ToolOutput:
    hits = await hybrid_search(ctx.repo_id, args.query, ctx.embedder, ctx.reranker,
                               repo_embed_model=ctx.repo["stats"].get("embed_model"),
                               k=args.k, path_prefix=args.path_prefix)
    if not hits:
        return ToolOutput("No matches. Try other words, an identifier, or grep.")
    parts = []
    for n, hit in enumerate(hits, 1):
        lines = hit.content.split("\n")
        shown = lines[:60]
        scope = f"  ({hit.scope})" if hit.scope else ""
        body = numbered(shown, hit.start_line)
        more = f"\n   ... {len(lines) - 60} more lines; use read_file" if len(lines) > 60 else ""
        parts.append(f"[{n}] {hit.path}:{hit.start_line}-{hit.end_line}{scope}\n{body}{more}")
    sources = [{"path": h.path, "start_line": h.start_line, "end_line": h.end_line} for h in hits]
    return ToolOutput("\n\n".join(parts), sources)


async def _similar_paths(ctx: ToolContext, path: str) -> list[str]:
    name = path.rstrip("/").split("/")[-1]
    async with connection() as conn:
        rows = await (await conn.execute(
            "SELECT path FROM files WHERE repo_id = %s AND path ILIKE %s ORDER BY length(path) LIMIT 8",
            [ctx.repo_id, f"%{name}%"],
        )).fetchall()
    return [r["path"] for r in rows]


async def _read_file(ctx: ToolContext, args: ReadFileArgs) -> ToolOutput:
    path = args.path.strip().lstrip("./")
    async with connection() as conn:
        row = await (await conn.execute(
            "SELECT content, line_count FROM files WHERE repo_id = %s AND path = %s", [ctx.repo_id, path],
        )).fetchone()
    if row is None:
        similar = await _similar_paths(ctx, path)
        hint = ("Similar paths: " + ", ".join(similar)) if similar else "Use search_code or grep to find it."
        return ToolOutput(f"File not found: {path}. {hint}", is_error=True)
    lines = row["content"].split("\n")
    start = args.start_line or 1
    end = min(args.end_line or len(lines), len(lines), start + MAX_READ_LINES - 1)
    if start > len(lines):
        return ToolOutput(f"{path} has only {len(lines)} lines.", is_error=True)
    text = f"{path}:{start}-{end} (of {len(lines)} lines)\n{numbered(lines[start - 1:end], start)}"
    if end < len(lines) and (args.end_line is None or args.end_line > end):
        text += f"\n... continues; read from line {end + 1}"
    return ToolOutput(text, [{"path": path, "start_line": start, "end_line": end}])


def _glob_match(path: str, pattern: str) -> bool:
    if fnmatch.fnmatch(path, pattern):
        return True
    return "/" not in pattern and fnmatch.fnmatch(path.rsplit("/", 1)[-1], pattern)


async def _grep(ctx: ToolContext, args: GrepArgs) -> ToolOutput:
    try:
        regex = re.compile(args.pattern, 0 if args.case_sensitive else re.IGNORECASE)
    except re.error as exc:
        return ToolOutput(f"Invalid regex: {exc}", is_error=True)
    async with connection() as conn:
        rows = await (await conn.execute(
            "SELECT path, content FROM files WHERE repo_id = %s ORDER BY path", [ctx.repo_id],
        )).fetchall()
    matches: list[tuple[str, int, str]] = []
    files_hit: Counter[str] = Counter()
    for row in rows:
        if args.path_glob and not _glob_match(row["path"], args.path_glob):
            continue
        for number, line in enumerate(row["content"].split("\n"), 1):
            if regex.search(line):
                files_hit[row["path"]] += 1
                if len(matches) < args.max_results:
                    matches.append((row["path"], number, line.strip()[:200]))
    if not matches:
        return ToolOutput(f"No lines match /{args.pattern}/.")
    total = sum(files_hit.values())
    text = "\n".join(f"{p}:{n}: {line}" for p, n, line in matches)
    if total > len(matches):
        text += f"\n... {total - len(matches)} more matches in {len(files_hit)} files; narrow with path_glob"
    sources = [{"path": p, "start_line": n, "end_line": n} for p, n, _ in matches[:20]]
    return ToolOutput(text, sources)


async def _find_symbol(ctx: ToolContext, args: FindSymbolArgs) -> ToolOutput:
    name = args.name.strip()
    kind_filter, params = ("", [])
    if args.kind:
        kind_filter, params = " AND kind = %s", [args.kind.lower()]
    sql = ("SELECT name, qualified, kind, path, start_line, end_line, signature FROM symbols "
           "WHERE repo_id = %s AND ({cond}){kind} ORDER BY length(qualified), path LIMIT 25")
    async with connection() as conn:
        rows = await (await conn.execute(
            sql.format(cond="lower(name) = lower(%s) OR lower(qualified) = lower(%s)", kind=kind_filter),
            [ctx.repo_id, name, name, *params],
        )).fetchall()
        if not rows:
            rows = await (await conn.execute(
                sql.format(cond="qualified ILIKE %s", kind=kind_filter),
                [ctx.repo_id, f"%{name}%", *params],
            )).fetchall()
    if not rows:
        return ToolOutput(f"No symbol named '{name}'. Try grep or search_code.")
    text = "\n".join(f"{r['kind']} {r['qualified']}  {r['path']}:{r['start_line']}-{r['end_line']}\n"
                     f"    {r['signature']}" for r in rows)
    sources = [{"path": r["path"], "start_line": r["start_line"], "end_line": r["end_line"]} for r in rows]
    return ToolOutput(text, sources)


async def _list_endpoints(ctx: ToolContext, args: ListEndpointsArgs) -> ToolOutput:
    conds, params = ["repo_id = %s"], [ctx.repo_id]
    if args.method:
        conds.append("(upper(method) = upper(%s) OR method = 'MOUNT')")
        params.append(args.method)
    if args.route_contains:
        conds.append("route ILIKE %s")
        params.append(f"%{args.route_contains}%")
    async with connection() as conn:
        rows = await (await conn.execute(
            f"SELECT framework, method, route, handler, path, line FROM endpoints WHERE {' AND '.join(conds)} "
            "ORDER BY path, line", params,
        )).fetchall()
    if not rows:
        return ToolOutput("No endpoints found by static extraction. Try grep for routing code.")
    mounts = [r for r in rows if r["method"] == "MOUNT"]
    routes = [r for r in rows if r["method"] != "MOUNT"]
    out = [f"{len(routes)} endpoints:"]
    out += [f"{r['method']:<7} {r['route']:<40} -> {r['handler'] or '?'}  [{r['framework']}] {r['path']}:{r['line']}"
            for r in routes]
    if mounts:
        out.append(f"\n{len(mounts)} router mounts (prefixes applied where these routers are included):")
        out += [f"MOUNT   {r['route'] or '/':<40} <- {r['handler']}  [{r['framework']}] {r['path']}:{r['line']}"
                for r in mounts]
    sources = [{"path": r["path"], "start_line": r["line"], "end_line": r["line"]} for r in rows[:40]]
    return ToolOutput("\n".join(out), sources)


async def _list_dependencies(ctx: ToolContext, args: ListDependenciesArgs) -> ToolOutput:
    conds, params = ["repo_id = %s"], [ctx.repo_id]
    if args.name_contains:
        conds.append("name ILIKE %s")
        params.append(f"%{args.name_contains}%")
    if args.ecosystem:
        conds.append("ecosystem = %s")
        params.append(args.ecosystem.lower())
    async with connection() as conn:
        rows = await (await conn.execute(
            f"SELECT ecosystem, name, version_spec, scope, manifest_path FROM dependencies "
            f"WHERE {' AND '.join(conds)} ORDER BY manifest_path, scope, name", params,
        )).fetchall()
    if not rows:
        return ToolOutput("No declared dependencies match.")
    by_manifest: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_manifest[r["manifest_path"]].append(r)
    out = []
    for manifest, deps in by_manifest.items():
        out.append(f"{manifest} ({deps[0]['ecosystem']}, {len(deps)} packages)")
        out += [f"  {d['name']} {d['version_spec'] or ''}  [{d['scope'] or 'main'}]".rstrip() for d in deps]
    sources = [{"path": m, "start_line": 1, "end_line": 1} for m in by_manifest]
    return ToolOutput("\n".join(out), sources)


async def _find_imports(ctx: ToolContext, args: FindImportsArgs) -> ToolOutput:
    async with connection() as conn:
        rows = await (await conn.execute(
            "SELECT module, path, line FROM imports WHERE repo_id = %s AND module ILIKE %s "
            "ORDER BY module, path LIMIT 150",
            [ctx.repo_id, f"%{args.module_contains}%"],
        )).fetchall()
    if not rows:
        return ToolOutput(f"No imports of modules matching '{args.module_contains}'.")
    text = "\n".join(f"{r['path']}:{r['line']} imports {r['module']}" for r in rows)
    sources = [{"path": r["path"], "start_line": r["line"], "end_line": r["line"]} for r in rows[:20]]
    return ToolOutput(text, sources)


async def _repo_overview(ctx: ToolContext, _: RepoOverviewArgs) -> ToolOutput:
    repo, stats = ctx.repo, ctx.repo["stats"]
    async with connection() as conn:
        paths = [r["path"] for r in await (await conn.execute(
            "SELECT path FROM files WHERE repo_id = %s ORDER BY path", [ctx.repo_id])).fetchall()]
        readme = await (await conn.execute(
            "SELECT path, content FROM files WHERE repo_id = %s AND path ~* '^readme' ORDER BY length(path) LIMIT 1",
            [ctx.repo_id])).fetchone()
        manifests = await (await conn.execute(
            "SELECT manifest_path, ecosystem, count(*) AS n FROM dependencies WHERE repo_id = %s "
            "GROUP BY manifest_path, ecosystem ORDER BY manifest_path", [ctx.repo_id])).fetchall()
        frameworks = await (await conn.execute(
            "SELECT framework, count(*) AS n FROM endpoints WHERE repo_id = %s AND method <> 'MOUNT' "
            "GROUP BY framework", [ctx.repo_id])).fetchall()

    top: Counter[str] = Counter()
    second: dict[str, Counter[str]] = defaultdict(Counter)
    for p in paths:
        parts = p.split("/")
        top[parts[0] + ("/" if len(parts) > 1 else "")] += 1
        if len(parts) > 2:
            second[parts[0]][parts[1] + "/"] += 1
    tree = []
    for name, count in sorted(top.items()):
        tree.append(f"{name}  ({count} files)" if name.endswith("/") else name)
        for sub, n in sorted(second.get(name.rstrip("/"), {}).items())[:12]:
            tree.append(f"    {sub}  ({n})")

    out = [
        f"Repository: {repo['name']}  commit {repo.get('commit_sha') or 'unknown'}",
        f"{stats.get('files', 0)} files, {stats.get('lines', 0)} lines, {stats.get('symbols', 0)} symbols",
        "Languages (files): " + ", ".join(f"{k} {v}" for k, v in stats.get("languages", {}).items()),
        "Endpoints: " + (", ".join(f"{r['framework']} {r['n']}" for r in frameworks) or "none found"),
        "Manifests: " + (", ".join(f"{r['manifest_path']} ({r['ecosystem']}, {r['n']})" for r in manifests)
                         or "none"),
        "",
        "Layout:",
        *tree[:80],
    ]
    sources = []
    if readme:
        lines = readme["content"].split("\n")[:60]
        out += ["", f"{readme['path']} (first {len(lines)} lines):", *lines]
        sources.append({"path": readme["path"], "start_line": 1, "end_line": len(lines)})
    return ToolOutput("\n".join(out), sources)


_HANDLERS = {
    "repo_overview": _repo_overview,
    "search_code": _search_code,
    "read_file": _read_file,
    "grep": _grep,
    "find_symbol": _find_symbol,
    "list_endpoints": _list_endpoints,
    "list_dependencies": _list_dependencies,
    "find_imports": _find_imports,
    **graph_tools.HANDLERS,
}


async def run_tool(ctx: ToolContext, name: str, raw_input: Any) -> ToolOutput:
    model = ARG_MODELS.get(name)
    if model is None:
        return ToolOutput(f"Unknown tool: {name}", is_error=True)
    if not isinstance(raw_input, dict):
        return ToolOutput(f'{{"INVALID_JSON": {str(raw_input)[:500]!r}}}', is_error=True)
    try:
        args = model.model_validate(raw_input)
    except ValidationError as exc:
        return ToolOutput(f"Invalid arguments for {name}: {exc.errors(include_url=False)}", is_error=True)
    return await _HANDLERS[name](ctx, args)
