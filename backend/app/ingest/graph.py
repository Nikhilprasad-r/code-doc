"""Code graph, built by graphify (https://github.com/safishamsi/graphify, MIT licence; pinned in pyproject).

graphify parses the checkout with tree-sitter, without LLM calls. It writes graph.json, with symbols
and files as nodes, links for calls, references, imports, inheritance and membership, and a community
(a cluster of closely linked code) for each node.

It runs as a subprocess, and two settings keep it contained:
- GRAPHIFY_OUT points at a directory under DATA_DIR, so nothing is written into the repository.
- The process gets a minimal environment, so no API keys reach it.

graph.json (networkx node-link JSON) is the only contract with graphify.

`graph_rows` turns graph.json into the rows stored in Postgres:
- It keeps only nodes in files this index holds, and skips data and doc files (JSON keys, Markdown
  headings).
- It matches symbols to our own symbol table, so each one has a full line range to cite.
- It merges unresolved names (library types, annotations) into one external node per name.
- It labels each community by its most connected symbols and its main directory.

graphify's graph is a simple graph: when code is linked to the same thing twice (say it both calls and
references it), only one of the links is kept.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Protocol

from app.config import Settings
from app.db import connection

log = logging.getLogger(__name__)

# Files whose graphify nodes are not code: config keys, document headings.
DATA_LANGUAGES = {"markdown", "text", "yaml", "json", "toml", "xml", "properties", "html", "css"}
# Links that state membership (file contains class, class has method) rather than use.
STRUCTURAL = {"contains", "defines", "method"}
# Links that mean "depends on", followed backwards to find what a change affects (graphify's own set).
DEPENDENCY = {"calls", "indirect_call", "references", "imports", "imports_from", "re_exports", "inherits",
              "extends", "implements", "uses", "mixes_in", "embeds"}
_ENV_PASSTHROUGH = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")


class GraphError(Exception):
    pass


class SymbolLike(Protocol):
    name: str
    qualified: str
    kind: str
    start_line: int
    end_line: int


@dataclass
class GraphNode:
    id: str
    label: str            # what to show: 'ItemService.archive', 'api/routes/items.py', 'Session'
    name: str             # bare name to look up: 'archive'
    kind: str             # file, class, method, function, ... symbol, external
    path: str | None
    start_line: int | None
    end_line: int | None
    community: int | None
    degree: int = 0


@dataclass
class GraphEdge:
    source: str
    target: str
    relation: str
    inferred: bool
    path: str | None      # where the link is written in the code
    line: int | None


@dataclass
class Community:
    id: int
    label: str
    size: int
    directory: str | None
    top: list[str] = field(default_factory=list)                      # node ids, most connected first
    links: list[tuple[int, int]] = field(default_factory=list)        # (other community, link count)


@dataclass
class CodeGraph:
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    communities: list[Community]

    def stats(self) -> dict:
        return {
            "nodes": sum(1 for n in self.nodes if n.kind != "external"),
            "external": sum(1 for n in self.nodes if n.kind == "external"),
            "edges": len(self.edges),
            "communities": len(self.communities),
        }


# --- running graphify -------------------------------------------------------------------------------

async def run_graphify(root: Path, out_dir: Path, timeout_s: float) -> dict:
    """Build the graph for `root` and return graph.json. Keeps graphify's per-file cache between runs
    but nothing else, so every run is a full rebuild."""
    out_dir = out_dir.resolve()
    if out_dir.exists():
        for child in out_dir.iterdir():
            if child.name == "cache":
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)
    env = {k: os.environ[k] for k in _ENV_PASSTHROUGH if k in os.environ}
    env |= {"GRAPHIFY_OUT": str(out_dir), "GRAPHIFY_NO_TIPS": "1", "PYTHONUNBUFFERED": "1"}
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "graphify", "update", str(root.resolve()),
        cwd=out_dir, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.wait_for(proc.communicate(), timeout_s)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise GraphError(f"graphify did not finish within {timeout_s:.0f}s") from None
    except asyncio.CancelledError:  # indexing failed elsewhere: don't leave graphify running
        proc.kill()
        await proc.wait()
        raise
    graph_file = out_dir / "graph.json"
    if proc.returncode != 0 or not graph_file.exists():
        tail = " | ".join(output.decode(errors="replace").strip().splitlines()[-3:])
        raise GraphError(f"graphify exited with {proc.returncode}: {tail[:500]}")
    return await asyncio.to_thread(lambda: json.loads(graph_file.read_text(encoding="utf-8")))


# --- graph.json -> rows ----------------------------------------------------------------------------

def _line(location: object) -> int | None:
    match = re.match(r"L(\d+)", str(location or ""))
    return int(match[1]) if match else None


def bare_name(label: str) -> str:
    """'.addVisit()' -> 'addVisit', 'get_user()' -> 'get_user', '{ authenticate }' -> 'authenticate'."""
    name = label.strip().strip("{} ").strip()
    name = name.removeprefix(".")
    return name.removesuffix("()")


def _match_symbol(symbols: list[SymbolLike], name: str, line: int | None) -> SymbolLike | None:
    candidates = [s for s in symbols if s.name == name]
    if not candidates:
        return None
    if line is None:
        return candidates[0] if len(candidates) == 1 else None
    containing = [s for s in candidates if s.start_line <= line <= s.end_line]
    if containing:
        return min(containing, key=lambda s: s.end_line - s.start_line)
    near = [s for s in candidates if abs(s.start_line - line) <= 3]
    return min(near, key=lambda s: abs(s.start_line - line)) if near else None


def _common_directory(paths: list[str]) -> str | None:
    """The deepest directory holding at least 60% of `paths`."""
    if not paths:
        return None
    counts: Counter[str] = Counter()
    for path in paths:
        parts = PurePosixPath(path).parts[:-1]
        for depth in range(1, len(parts) + 1):
            counts["/".join(parts[:depth]) + "/"] += 1
    qualifying = [d for d, n in counts.items() if n >= 0.6 * len(paths)]
    return max(qualifying, key=lambda d: d.count("/")) if qualifying else None


def graph_rows(data: dict, files: dict[str, tuple[str | None, int]],
               symbols: dict[str, list[SymbolLike]]) -> CodeGraph:
    """`files` maps each indexed path to (language, line count); `symbols` maps a path to its symbols."""
    nodes: dict[str, GraphNode] = {}
    alias: dict[str, str] = {}        # graphify node id -> our node id (external names are merged)
    unmatched: set[str] = set()
    for raw in data.get("nodes", []):
        if raw.get("file_type", "code") != "code":
            continue
        label = str(raw.get("label") or raw.get("id"))
        name = bare_name(label)
        source_file = str(raw.get("source_file") or "").strip()
        community = raw.get("community")
        if not source_file:
            if not name:
                continue
            node_id = f"external:{name}"
            alias[raw["id"]] = node_id
            nodes.setdefault(node_id, GraphNode(node_id, name, name, "external", None, None, None, community))
            continue
        info = files.get(source_file)
        if info is None or info[0] in DATA_LANGUAGES:
            continue
        line = _line(raw.get("source_location"))
        if line in (None, 1) and (label == PurePosixPath(source_file).name or source_file == label
                                  or source_file.endswith("/" + label)):
            # A file, named by its file name or as an import target ('src/utils.ts'): one node per file.
            node_id = f"file:{source_file}"
            alias[raw["id"]] = node_id
            nodes.setdefault(node_id, GraphNode(node_id, source_file, PurePosixPath(source_file).name, "file",
                                                source_file, 1, info[1], community))
            continue
        node_id = str(raw["id"])
        alias[raw["id"]] = node_id
        symbol = _match_symbol(symbols.get(source_file, []), name, line)
        if symbol is not None:
            nodes[node_id] = GraphNode(node_id, symbol.qualified, name, symbol.kind, source_file,
                                       symbol.start_line, symbol.end_line, community)
        else:
            kind = "method" if label.startswith(".") else "function" if label.endswith("()") else "symbol"
            nodes[node_id] = GraphNode(node_id, name, name, kind, source_file, line, line, community)
            unmatched.add(node_id)

    edges: list[GraphEdge] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in data.get("links", data.get("edges", [])):
        source, target = alias.get(raw.get("source")), alias.get(raw.get("target"))
        relation = str(raw.get("relation") or "related")
        if not source or not target or source == target or (source, target, relation) in seen:
            continue
        seen.add((source, target, relation))
        path = raw.get("source_file") if raw.get("source_file") in files else None
        edges.append(GraphEdge(source, target, relation, raw.get("confidence", "EXTRACTED") != "EXTRACTED",
                               path, _line(raw.get("source_location")) if path else None))

    # Methods we could not match to our symbol table still get a qualified label from their class.
    for edge in edges:
        if edge.relation == "method" and edge.target in unmatched:
            nodes[edge.target].label = f"{nodes[edge.source].label}.{nodes[edge.target].name}"

    neighbours: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        if edge.relation != "contains":
            neighbours[edge.source].add(edge.target)
            neighbours[edge.target].add(edge.source)
    for node in nodes.values():
        node.degree = len(neighbours[node.id])

    return CodeGraph(list(nodes.values()), edges, _communities(nodes, edges))


def _communities(nodes: dict[str, GraphNode], edges: list[GraphEdge]) -> list[Community]:
    members: dict[int, list[GraphNode]] = defaultdict(list)
    for node in nodes.values():
        if node.community is not None and node.kind != "external":
            members[node.community].append(node)
    between: dict[int, Counter[int]] = defaultdict(Counter)
    for edge in edges:
        a, b = nodes[edge.source], nodes[edge.target]
        if edge.relation in STRUCTURAL or a.kind == "external" or b.kind == "external":
            continue
        if a.community is not None and b.community is not None and a.community != b.community:
            between[a.community][b.community] += 1
            between[b.community][a.community] += 1
    communities = []
    for cid, group in members.items():
        ranked = sorted(group, key=lambda n: (n.kind == "file", -n.degree, n.label))
        symbols = [n for n in ranked if n.kind != "file"]
        label = ", ".join(n.label for n in (symbols or ranked)[:3])
        directory = _common_directory([n.path for n in group if n.path])
        communities.append(Community(cid, label, len(group), directory, [n.id for n in ranked[:12]],
                                     between[cid].most_common(6)))
    communities.sort(key=lambda c: -c.size)
    return communities


# --- storage --------------------------------------------------------------------------------------

async def write_graph(repo_id: int, graph: CodeGraph | None) -> None:
    """Replace the stored graph (`None` clears it, so a failed build never leaves a stale graph)."""
    async with connection() as conn, conn.transaction():
        for table in ("graph_nodes", "graph_edges", "graph_communities"):
            await conn.execute(f"DELETE FROM {table} WHERE repo_id = %s", [repo_id])
        if graph is None:
            return
        async with conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO graph_nodes (repo_id, node_id, label, name, kind, path, start_line, end_line, "
                "community, degree) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [(repo_id, n.id, n.label, n.name, n.kind, n.path, n.start_line, n.end_line, n.community,
                  n.degree) for n in graph.nodes],
            )
            await cur.executemany(
                "INSERT INTO graph_edges (repo_id, source, target, relation, inferred, path, line) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [(repo_id, e.source, e.target, e.relation, e.inferred, e.path, e.line) for e in graph.edges],
            )
            await cur.executemany(
                "INSERT INTO graph_communities (repo_id, community, label, size, directory, top_nodes, links) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [(repo_id, c.id, c.label, c.size, c.directory, json.dumps(c.top), json.dumps(c.links))
                 for c in graph.communities],
            )


async def build_code_graph(repo_id: int, root: Path, files: list, settings: Settings) -> dict:
    """Run graphify on the checkout and store the result. Never raises: the code graph is an extra, so
    a failure is recorded in the stats and indexing carries on without it."""
    started = time.monotonic()
    try:
        data = await run_graphify(root, settings.data_dir / "graphs" / str(repo_id), settings.graph_timeout_s)
        graph = await asyncio.to_thread(
            graph_rows,
            data,
            {f.path: (f.language, f.text.count("\n") + 1) for f in files},
            {f.path: f.symbols for f in files if f.symbols},
        )
        await write_graph(repo_id, graph)
        return graph.stats() | {"seconds": round(time.monotonic() - started, 1)}
    except Exception as exc:
        log.warning("code graph for repo %s failed: %s", repo_id, exc, exc_info=not isinstance(exc, GraphError))
        try:
            await write_graph(repo_id, None)
        except Exception:
            log.exception("could not clear the code graph for repo %s", repo_id)
        return {"error": str(exc)[:500]}
