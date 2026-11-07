"""Tools over the code graph (app/ingest/graph.py): architecture, related, impact.

graphify finds links by static analysis. References, imports, inheritance and membership come
straight from the syntax tree. Calls through an object or module (`owner.addVisit()`,
`crud.authenticate()`) are resolved from types and imports, and the ones it had to infer are marked.
Calls it cannot resolve are missing: through untyped variables, callbacks, reflection, or framework
wiring. The tool descriptions and outputs say so, and point the model to grep for complete call sites.
"""

from collections import defaultdict

from pydantic import Field

from app.agent.toolbase import Args, ToolContext, ToolOutput
from app.db import connection
from app.ingest.graph import DEPENDENCY, STRUCTURAL, bare_name

INHERITANCE = {"inherits", "extends", "implements", "mixes_in", "embeds"}
CALLS_CAVEAT = ("Calls come from static analysis: those through untyped variables, callbacks or framework "
                "wiring can be missing, so grep for the name before concluding nothing calls it.")
MAX_PER_GROUP = 40
MAX_PER_HOP = 50


class ArchitectureArgs(Args):
    module: int | None = Field(None, ge=0, description="A module number from an earlier architecture call, "
                                                       "to list that module's members and its links.")


class RelatedArgs(Args):
    symbol: str = Field(min_length=1, description="Class, function, method ('Cls.method') or file path.")
    path: str | None = Field(None, description="Part of the file path, to choose between symbols that share "
                                               "a name.")


class ImpactArgs(RelatedArgs):
    depth: int = Field(2, ge=1, le=4, description="How many steps of dependants to follow.")


DEFINITIONS = [
    ("architecture",
     "Map of the codebase from its code graph. It lists the most connected symbols and the modules: "
     "clusters of code that link mostly to each other, with their main directory and the modules they "
     "connect to. Pass `module` to list one module's members. Use it for architecture and 'how is this "
     "organised' questions, after repo_overview.",
     ArchitectureArgs),
    ("related",
     "A symbol's neighbours in the code graph: where it is defined, its members, what it inherits, what "
     "it uses (references, calls, imports) and what uses it. Each comes with a file and line. " + CALLS_CAVEAT,
     RelatedArgs),
    ("impact",
     "What depends on a symbol, directly and through other code (calls, references, imports, inheritance), "
     "grouped by distance: the code a change to it could affect. For a class or file this includes the "
     "users of its members. " + CALLS_CAVEAT,
     ImpactArgs),
]


# --- helpers ---------------------------------------------------------------------------------------

def _graph_missing(ctx: ToolContext) -> ToolOutput | None:
    graph = (ctx.repo.get("stats") or {}).get("graph") or {}
    if graph.get("nodes"):
        return None
    reason = f" (building it failed: {graph['error']})" if graph.get("error") else ""
    return ToolOutput(f"This repository has no code graph{reason}. Use find_symbol, grep and find_imports "
                      "instead.")


def _location(node: dict) -> str:
    if not node.get("path"):
        return "(not defined in this repository)"
    start, end = node.get("start_line"), node.get("end_line")
    if not start:
        return node["path"]
    return f"{node['path']}:{start}" if not end or end == start else f"{node['path']}:{start}-{end}"


def _describe(node: dict) -> str:
    return f"{node['label']} ({node['kind']}) {_location(node)}"


def _source(node: dict) -> dict | None:
    if not node.get("path") or not node.get("start_line"):
        return None
    return {"path": node["path"], "start_line": node["start_line"],
            "end_line": node.get("end_line") or node["start_line"]}


def _sources(nodes: list[dict], limit: int = 15) -> list[dict]:
    out, seen = [], set()
    for node in nodes:
        source = _source(node)
        if source and (key := tuple(source.values())) not in seen:
            seen.add(key)
            out.append(source)
        if len(out) >= limit:
            break
    return out


_ORDER = "ORDER BY (kind = 'external'), (kind = 'file'), degree DESC, label LIMIT 10"


async def _find_nodes(ctx: ToolContext, symbol: str, path: str | None) -> list[dict]:
    """Nodes for a symbol name, 'Cls.method', 'module.func' or a file path, best match first."""
    query = symbol.strip()
    name = bare_name(query)
    path_cond, path_params = ("AND path ILIKE %s", [f"%{path.strip()}%"]) if path else ("", [])
    async with connection() as conn:
        async def fetch(cond: str, params: list) -> list[dict]:
            return await (await conn.execute(
                f"SELECT * FROM graph_nodes WHERE repo_id = %s AND ({cond}) {path_cond} {_ORDER}",
                [ctx.repo_id, *params, *path_params])).fetchall()

        rows = await fetch("lower(label) = lower(%s) OR (kind = 'file' AND lower(path) = lower(%s))",
                           [query, query.lstrip("./")])
        if not rows and "." in name and "/" not in name:
            # 'crud.authenticate', 'owner.addVisit': a name qualified by a module or class
            prefix, last = name.rsplit(".", 1)
            rows = await fetch("lower(name) = lower(%s) AND (label ILIKE %s OR path ILIKE %s)",
                               [last, f"%{prefix}.%", f"%{prefix}%"])
        if not rows:
            rows = await fetch("lower(name) = lower(%s)", [name])
        if not rows and "/" in query:
            rows = await fetch("kind = 'file' AND path ILIKE %s", [f"%{query.lstrip('./')}%"])
    return rows


async def _resolve(ctx: ToolContext, symbol: str, path: str | None) -> tuple[dict | None, str]:
    """The node to use, plus a note listing other matches (or a not-found message)."""
    rows = await _find_nodes(ctx, symbol, path)
    if not rows:
        async with connection() as conn:
            similar = await (await conn.execute(
                "SELECT label FROM graph_nodes WHERE repo_id = %s AND label ILIKE %s AND kind <> 'external' "
                "ORDER BY degree DESC LIMIT 8", [ctx.repo_id, f"%{bare_name(symbol)}%"])).fetchall()
        hint = ("Similar names: " + ", ".join(r["label"] for r in similar)) if similar else \
            "Try find_symbol or grep."
        return None, f"'{symbol}' is not in the code graph. {hint}"
    others = rows[1:]
    note = ""
    if others:
        note = ("Also matching (pass `path` to choose): "
                + "; ".join(_describe(n) for n in others[:6]) + "\n")
    return rows[0], note


async def _neighbours(ctx: ToolContext, node_id: str) -> tuple[list[dict], list[dict]]:
    """(outgoing, incoming) links of a node, each joined with the node at the other end."""
    sql = ("SELECT e.relation, e.inferred, e.path AS at_path, e.line AS at_line, n.* FROM graph_edges e "
           "JOIN graph_nodes n ON n.repo_id = e.repo_id AND n.node_id = e.{other} "
           "WHERE e.repo_id = %s AND e.{this} = %s ORDER BY n.kind = 'external', n.path, n.start_line, n.label")
    async with connection() as conn:
        outgoing = await (await conn.execute(sql.format(other="target", this="source"),
                                             [ctx.repo_id, node_id])).fetchall()
        incoming = await (await conn.execute(sql.format(other="source", this="target"),
                                             [ctx.repo_id, node_id])).fetchall()
    return outgoing, incoming


def _link_line(row: dict) -> str:
    at = f"  at {row['at_path']}:{row['at_line']}" if row.get("at_path") and row.get("at_line") else ""
    guess = " (inferred)" if row.get("inferred") else ""
    return f"  {row['relation']:<12} {_describe(row)}{at}{guess}"


def _group(title: str, rows: list[dict], out: list[str]) -> None:
    if not rows:
        return
    out.append(f"{title} ({len(rows)}):")
    out += [_link_line(r) for r in rows[:MAX_PER_GROUP]]
    if len(rows) > MAX_PER_GROUP:
        out.append(f"  ... {len(rows) - MAX_PER_GROUP} more")


# --- architecture ----------------------------------------------------------------------------------

async def architecture(ctx: ToolContext, args: ArchitectureArgs) -> ToolOutput:
    if missing := _graph_missing(ctx):
        return missing
    async with connection() as conn:
        communities = await (await conn.execute(
            "SELECT * FROM graph_communities WHERE repo_id = %s ORDER BY size DESC, community",
            [ctx.repo_id])).fetchall()
        labels = {c["community"]: c["label"] for c in communities}
        if args.module is not None:
            return await _module(ctx, conn, args.module, communities, labels)
        top = await (await conn.execute(
            "SELECT * FROM graph_nodes WHERE repo_id = %s AND kind NOT IN ('file', 'external') "
            "ORDER BY degree DESC, label LIMIT 12", [ctx.repo_id])).fetchall()
        external = await (await conn.execute(
            "SELECT label, degree FROM graph_nodes WHERE repo_id = %s AND kind = 'external' "
            "ORDER BY degree DESC, label LIMIT 10", [ctx.repo_id])).fetchall()

    graph = ctx.repo["stats"]["graph"]
    out = [f"Code graph of {ctx.repo['name']}: {graph['nodes']} files and symbols, {graph['edges']} links, in "
           f"{len(communities)} modules (clusters of code that link mostly to each other).", "",
           "Most connected symbols:"]
    out += [f"  {_describe(n)}: {n['degree']} links, module [{n['community']}]" for n in top]
    out += ["", "Modules, largest first:"]
    shown = communities[:15]
    for c in shown:
        where = f", mostly in {c['directory']}" if c["directory"] else ""
        out.append(f"  [{c['community']}] {c['label']}: {c['size']} nodes{where}")
        if c["links"]:
            out.append("      links to " + ", ".join(f"[{other}] ({n})" for other, n in c["links"][:4]))
    if len(communities) > len(shown):
        rest = communities[len(shown):]
        out.append(f"  ... and {len(rest)} smaller modules ({sum(c['size'] for c in rest)} nodes)")
    if external:
        out += ["", "Most used names defined outside the repository (libraries, frameworks): "
                + ", ".join(f"{e['label']} ({e['degree']})" for e in external)]
    out += ["", "architecture(module=N) lists a module's members; related and impact take one symbol."]
    return ToolOutput("\n".join(out), _sources(top, 10))


async def _module(ctx: ToolContext, conn, module: int, communities: list[dict],
                  labels: dict[int, str]) -> ToolOutput:
    community = next((c for c in communities if c["community"] == module), None)
    if community is None:
        return ToolOutput(f"No module [{module}]. Modules: "
                          + ", ".join(str(c["community"]) for c in communities[:40]), is_error=True)
    members = await (await conn.execute(
        "SELECT * FROM graph_nodes WHERE repo_id = %s AND community = %s AND kind <> 'external' "
        "ORDER BY degree DESC, label", [ctx.repo_id, module])).fetchall()
    by_file: dict[str, int] = defaultdict(int)
    for m in members:
        if m["path"]:
            by_file[m["path"]] += 1
    where = f", mostly in {community['directory']}" if community["directory"] else ""
    out = [f"Module [{module}] {community['label']}: {community['size']} nodes{where}", "", "Files:"]
    out += [f"  {path} ({n})" for path, n in sorted(by_file.items(), key=lambda kv: (-kv[1], kv[0]))[:25]]
    symbols = [m for m in members if m["kind"] != "file"]
    out += ["", "Members, most connected first:"]
    out += [f"  {_describe(m)}: {m['degree']} links" for m in symbols[:MAX_PER_GROUP]]
    if len(symbols) > MAX_PER_GROUP:
        out.append(f"  ... {len(symbols) - MAX_PER_GROUP} more")
    if community["links"]:
        out += ["", "Links to other modules:"]
        out += [f"  [{other}] {labels.get(other, '?')}: {n} links" for other, n in community["links"]]
    return ToolOutput("\n".join(out), _sources(symbols, 12))


# --- related ---------------------------------------------------------------------------------------

async def related(ctx: ToolContext, args: RelatedArgs) -> ToolOutput:
    if missing := _graph_missing(ctx):
        return missing
    node, note = await _resolve(ctx, args.symbol, args.path)
    if node is None:
        return ToolOutput(note)
    outgoing, incoming = await _neighbours(ctx, node["node_id"])
    module = f", module [{node['community']}]" if node.get("community") is not None else ""
    out = [f"{_describe(node)}{module}", *([note.rstrip()] if note else [])]

    parents = [r for r in incoming if r["relation"] in STRUCTURAL]
    if parents:
        out.append("Defined in: " + "; ".join(_describe(r) for r in parents[:3]))
    members = [r for r in outgoing if r["relation"] in STRUCTURAL]
    if members:
        listed = ", ".join(f"{r['name']}:{r['start_line']}" if r.get("start_line") else r["name"]
                           for r in members[:60])
        out.append(f"Members ({len(members)}): {listed}" + (" ..." if len(members) > 60 else ""))
    out.append("")
    _group("Inherits / implements", [r for r in outgoing if r["relation"] in INHERITANCE], out)
    _group("Subclasses / implementations", [r for r in incoming if r["relation"] in INHERITANCE], out)
    uses = [r for r in outgoing if r["relation"] not in STRUCTURAL | INHERITANCE]
    used_by = [r for r in incoming if r["relation"] not in STRUCTURAL | INHERITANCE]
    _group("Uses", uses, out)
    _group("Used by", used_by, out)
    if not (uses or used_by or members):
        out.append("No links in the graph.")
    if node["kind"] in {"function", "method", "constructor", "symbol"}:
        out += ["", CALLS_CAVEAT]
    rows = [node, *[r for r in incoming if r["relation"] not in STRUCTURAL], *outgoing]
    return ToolOutput("\n".join(out).rstrip(), _sources(rows))


# --- impact ----------------------------------------------------------------------------------------

async def impact(ctx: ToolContext, args: ImpactArgs) -> ToolOutput:
    if missing := _graph_missing(ctx):
        return missing
    node, note = await _resolve(ctx, args.symbol, args.path)
    if node is None:
        return ToolOutput(note)

    async with connection() as conn:
        # A class or file is affected through its members too: start from all of them.
        members = await (await conn.execute(
            "SELECT target FROM graph_edges WHERE repo_id = %s AND source = %s AND relation = ANY(%s)",
            [ctx.repo_id, node["node_id"], list(STRUCTURAL)])).fetchall()
        seed = {node["node_id"], *(m["target"] for m in members)}
        reached: dict[str, tuple[int, dict]] = {}
        frontier = set(seed)
        for hop in range(1, args.depth + 1):
            rows = await (await conn.execute(
                "SELECT source, target, relation, inferred, path, line FROM graph_edges "
                "WHERE repo_id = %s AND target = ANY(%s) AND relation = ANY(%s)",
                [ctx.repo_id, list(frontier), list(DEPENDENCY)])).fetchall()
            frontier = set()
            for row in rows:
                if row["source"] not in seed and row["source"] not in reached:
                    reached[row["source"]] = (hop, row)
                    frontier.add(row["source"])
            if not frontier:
                break
        ids = list(reached) + [r["target"] for _, r in reached.values()]
        nodes = {n["node_id"]: n for n in await (await conn.execute(
            "SELECT * FROM graph_nodes WHERE repo_id = %s AND node_id = ANY(%s)", [ctx.repo_id, ids])).fetchall()}

    extra = f" (and the users of its {len(seed) - 1} members)" if len(seed) > 1 else ""
    steps = f"{args.depth} step{'s' if args.depth > 1 else ''}"
    out = [f"What depends on {_describe(node)}{extra}, up to {steps} back along calls, references, imports "
           "and inheritance.", *([note.rstrip()] if note else [])]
    if not reached:
        out += ["Nothing in the graph depends on it.", "", CALLS_CAVEAT]
        return ToolOutput("\n".join(out), _sources([node]))

    by_hop: dict[int, list[tuple[dict, dict]]] = defaultdict(list)
    for node_id, (hop, row) in reached.items():
        if node_id in nodes:
            by_hop[hop].append((nodes[node_id], row))
    for hop in sorted(by_hop):
        group = sorted(by_hop[hop], key=lambda pair: (pair[0]["kind"] == "external", pair[0]["path"] or "",
                                                      pair[0]["start_line"] or 0))
        out += ["", f"{hop} step{'s' if hop > 1 else ''} away ({len(group)}):"]
        for dependant, row in group[:MAX_PER_HOP]:
            target = nodes.get(row["target"], {}).get("label", row["target"])
            at = f" at {row['path']}:{row['line']}" if row["path"] and row["line"] else ""
            guess = " (inferred)" if row["inferred"] else ""
            out.append(f"  {_describe(dependant)}: {row['relation']} {target}{at}{guess}")
        if len(group) > MAX_PER_HOP:
            out.append(f"  ... {len(group) - MAX_PER_HOP} more")
    files = sorted({n["path"] for n, _ in (pair for g in by_hop.values() for pair in g) if n["path"]})
    out += ["", f"{len(files)} files affected: " + ", ".join(files[:30]) + (" ..." if len(files) > 30 else ""),
            "", CALLS_CAVEAT]
    ordered = [nodes[i] for i in sorted(reached, key=lambda i: reached[i][0]) if i in nodes]
    return ToolOutput("\n".join(out), _sources([node, *ordered]))


HANDLERS = {"architecture": architecture, "related": related, "impact": impact}
