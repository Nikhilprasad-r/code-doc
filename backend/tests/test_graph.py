"""The code graph: graph.json -> rows (unit), and graphify on the fixture repo plus the graph tools
(integration)."""

from dataclasses import dataclass

import httpx
import pytest

from app.agent import answer
from app.agent.answer import answer_question
from app.agent.tools import ToolContext, has_graph, run_tool, tools_for
from app.config import get_settings
from app.db import connection
from app.ingest import graph as graph_module
from app.ingest.graph import GraphError, bare_name, graph_rows
from app.ingest.pipeline import index_repo
from app.main import app
from app.retrieval.embeddings import NoopReranker
from tests.conftest import FIXTURE_REPO
from tests.test_agent import ScriptedModel


@dataclass
class Sym:
    name: str
    qualified: str
    kind: str
    start_line: int
    end_line: int


GRAPH_JSON = {
    "directed": False, "multigraph": False, "graph": {},
    "nodes": [
        {"id": "f_svc", "label": "service.py", "file_type": "code", "source_file": "app/service.py",
         "source_location": "L1", "community": 0},
        {"id": "f_svc_import", "label": "app/service.py", "file_type": "code", "source_file": "app/service.py",
         "source_location": "L1", "community": 0},
        {"id": "cls", "label": "Service", "file_type": "code", "source_file": "app/service.py",
         "source_location": "L5", "community": 0},
        {"id": "m_run", "label": ".run()", "file_type": "code", "source_file": "app/service.py",
         "source_location": "L8", "community": 0},
        {"id": "m_stop", "label": ".stop()", "file_type": "code", "source_file": "app/service.py",
         "source_location": "L20", "community": 0},
        {"id": "f_api", "label": "api.py", "file_type": "code", "source_file": "app/api.py",
         "source_location": "L1", "community": 1},
        {"id": "handler", "label": "handle()", "file_type": "code", "source_file": "app/api.py",
         "source_location": "L3", "community": 1},
        {"id": "sess_a", "label": "Session", "file_type": "code", "source_file": "", "community": 0},
        {"id": "sess_b", "label": "Session", "file_type": "code", "source_file": "", "community": 1},
        {"id": "json_key", "label": "dependencies", "file_type": "code", "source_file": "package.json",
         "source_location": "L3", "community": 2},
        {"id": "heading", "label": "Usage", "file_type": "document", "source_file": "README.md",
         "source_location": "L4", "community": 2},
        {"id": "vendored", "label": "helper()", "file_type": "code", "source_file": "vendor/x.py",
         "source_location": "L1", "community": 1},
    ],
    "links": [
        {"source": "f_svc", "target": "cls", "relation": "contains", "confidence": "EXTRACTED",
         "source_file": "app/service.py", "source_location": "L5"},
        {"source": "cls", "target": "m_run", "relation": "method", "confidence": "EXTRACTED",
         "source_file": "app/service.py", "source_location": "L8"},
        {"source": "cls", "target": "m_stop", "relation": "method", "confidence": "EXTRACTED",
         "source_file": "app/service.py", "source_location": "L20"},
        {"source": "handler", "target": "cls", "relation": "calls", "confidence": "INFERRED",
         "source_file": "app/api.py", "source_location": "L4"},
        {"source": "handler", "target": "cls", "relation": "calls", "confidence": "INFERRED",
         "source_file": "app/api.py", "source_location": "L4"},
        {"source": "handler", "target": "sess_b", "relation": "references", "confidence": "EXTRACTED",
         "source_file": "app/api.py", "source_location": "L3"},
        {"source": "m_run", "target": "sess_a", "relation": "references", "confidence": "EXTRACTED",
         "source_file": "app/service.py", "source_location": "L8"},
        {"source": "f_api", "target": "f_svc_import", "relation": "imports", "confidence": "EXTRACTED",
         "source_file": "app/api.py", "source_location": "L1"},
        {"source": "f_api", "target": "vendored", "relation": "imports", "confidence": "EXTRACTED",
         "source_file": "app/api.py", "source_location": "L2"},
        {"source": "cls", "target": "cls", "relation": "references", "confidence": "EXTRACTED"},
    ],
}
FILES = {"app/service.py": ("python", 30), "app/api.py": ("python", 10), "package.json": ("json", 9),
         "README.md": ("markdown", 12)}
SYMBOLS = {"app/service.py": [Sym("Service", "Service", "class", 4, 25), Sym("run", "Service.run", "method", 7, 15)]}


def test_bare_name():
    assert bare_name(".addVisit()") == "addVisit"
    assert bare_name("get_user()") == "get_user"
    assert bare_name("{ authenticate }") == "authenticate"


def test_graph_rows_keeps_code_and_maps_it_to_our_symbols():
    graph = graph_rows(GRAPH_JSON, FILES, SYMBOLS)
    nodes = {n.id: n for n in graph.nodes}
    # Data keys, document headings and files outside the index are dropped.
    assert "json_key" not in nodes and "heading" not in nodes and "vendored" not in nodes
    # A file named by its file name and as an import target is one node with the whole file's range.
    service_file = nodes["file:app/service.py"]
    assert (service_file.kind, service_file.label, service_file.end_line) == ("file", "app/service.py", 30)
    assert "f_svc" not in nodes and "f_svc_import" not in nodes
    # Matched symbols take our qualified name, kind and full line range.
    assert (nodes["cls"].label, nodes["cls"].start_line, nodes["cls"].end_line) == ("Service", 4, 25)
    assert (nodes["m_run"].label, nodes["m_run"].kind, nodes["m_run"].end_line) == ("Service.run", "method", 15)
    # An unmatched method is still qualified by its class.
    assert (nodes["m_stop"].label, nodes["m_stop"].start_line) == ("Service.stop", 20)
    # Unresolved names are merged into one external node.
    assert nodes["external:Session"].kind == "external" and "sess_b" not in nodes

    edges = {(e.source, e.target, e.relation) for e in graph.edges}
    assert ("handler", "cls", "calls") in edges and len([e for e in graph.edges if e.relation == "calls"]) == 1
    assert ("handler", "external:Session", "references") in edges
    assert ("m_run", "external:Session", "references") in edges
    assert ("file:app/api.py", "file:app/service.py", "imports") in edges
    assert not any(e.source == e.target for e in graph.edges)
    call = next(e for e in graph.edges if e.relation == "calls")
    assert call.inferred and (call.path, call.line) == ("app/api.py", 4)

    # Degree counts distinct neighbours, ignoring file containment.
    assert nodes["cls"].degree == 3  # run, stop, handle
    assert nodes["external:Session"].degree == 2
    communities = {c.id: c for c in graph.communities}
    assert communities[0].label.startswith("Service") and communities[0].directory == "app/"
    assert dict(communities[0].links) == {1: 2}  # handle() calls Service; api.py imports service.py
    assert graph.stats() == {"nodes": 6, "external": 1, "edges": 7, "communities": 2}


# --- integration: graphify on the fixture repo -----------------------------------------------------

@pytest.fixture(scope="module")
def ctx(indexed_repo):
    repo_id, stats, embedder = indexed_repo
    repo = {"id": repo_id, "name": "sample", "source": "fixture", "commit_sha": None, "stats": stats}
    return ToolContext(repo=repo, embedder=embedder, reranker=NoopReranker())


@pytest.mark.integration
async def test_graph_is_built_outside_the_repository(indexed_repo):
    repo_id, stats, _ = indexed_repo
    assert stats["graph"]["nodes"] > 20 and stats["graph"]["edges"] > 20 and "error" not in stats["graph"]
    assert not (FIXTURE_REPO / "graphify-out").exists()
    assert (get_settings().data_dir / "graphs" / str(repo_id) / "graph.json").exists()
    async with connection() as conn:
        archive = await (await conn.execute(
            "SELECT * FROM graph_nodes WHERE repo_id = %s AND label = 'ItemService.archive'", [repo_id])).fetchone()
        data_nodes = await (await conn.execute(
            "SELECT count(*) AS n FROM graph_nodes WHERE repo_id = %s AND path IN ('web/package.json', 'README.md')",
            [repo_id])).fetchone()
    assert (archive["path"], archive["start_line"], archive["end_line"]) == ("api/routes/items.py", 42, 45)
    assert data_nodes["n"] == 0


@pytest.mark.integration
async def test_graph_view_is_served_sandboxed(indexed_repo):
    """graphify's graph.html carries text from the repository, so it must never run with the API's origin."""
    repo_id, _, _ = indexed_repo
    async with connection() as conn:
        bare = await (await conn.execute(
            "INSERT INTO repos (name, source) VALUES ('no-graph', '/nowhere') RETURNING id")).fetchone()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            page = await client.get(f"/repos/{repo_id}/graph.html")
            assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
            assert "vis-network" in page.text
            policy = page.headers["content-security-policy"]
            assert policy.startswith("sandbox allow-scripts;") and "allow-same-origin" not in policy
            assert "default-src 'none'" in policy
            assert f"frame-ancestors {' '.join(get_settings().cors_origins)}" in policy
            assert (await client.get(f"/repos/{bare['id']}/graph.html")).status_code == 404
            assert (await client.get("/repos/999999999/graph.html")).status_code == 404
    finally:
        async with connection() as conn:
            await conn.execute("DELETE FROM repos WHERE id = %s", [bare["id"]])


@pytest.mark.integration
async def test_related_lists_members_and_users(ctx):
    out = await run_tool(ctx, "related", {"symbol": "ItemService"})
    assert not out.is_error
    assert out.text.startswith("ItemService (class) api/routes/items.py:")
    assert "Members (2):" in out.text and "archive:42" in out.text

    out = await run_tool(ctx, "related", {"symbol": "SessionDep"})
    assert "Used by (3):" in out.text
    for user in ("read_items", "create_item", "delete_item"):
        assert user in out.text
    assert "at api/routes/items.py:12" in out.text
    assert {"path": "api/routes/items.py", "start_line": 11, "end_line": 14} in out.sources  # with its decorator


@pytest.mark.integration
async def test_impact_follows_dependants(ctx):
    out = await run_tool(ctx, "impact", {"symbol": "OwnerRepository"})
    assert "1 step away" in out.text
    assert "OwnerController (class) svc/src/main/java/com/example/petclinic/OwnerController.java" in out.text
    assert "files affected: svc/src/main/java/com/example/petclinic/OwnerController.java" in out.text

    out = await run_tool(ctx, "impact", {"symbol": "ItemService"})
    assert "(and the users of its 2 members)" in out.text and "Nothing in the graph depends on it." in out.text


@pytest.mark.integration
async def test_architecture_lists_modules(ctx):
    out = await run_tool(ctx, "architecture", {})
    assert "Most connected symbols:" in out.text and "Modules, largest first:" in out.text
    first = int(out.text.split("Modules, largest first:\n  [", 1)[1].split("]", 1)[0])
    module = await run_tool(ctx, "architecture", {"module": first})
    assert f"Module [{first}]" in module.text and "Members, most connected first:" in module.text
    missing = await run_tool(ctx, "architecture", {"module": 999})
    assert missing.is_error


@pytest.mark.integration
async def test_unknown_symbols_get_suggestions(ctx):
    out = await run_tool(ctx, "related", {"symbol": "Itemservic"})
    assert "is not in the code graph" in out.text and "ItemService" in out.text
    out = await run_tool(ctx, "related", {"symbol": "archive", "path": "routes/items"})
    assert out.text.startswith("ItemService.archive (method)")


@pytest.mark.integration
async def test_graph_failure_keeps_the_index_usable(indexed_repo, monkeypatch):
    async def fail(*_args, **_kwargs):
        raise GraphError("graphify exited with 1: boom")

    monkeypatch.setattr(graph_module, "run_graphify", fail)
    async with connection() as conn:
        repo = await (await conn.execute(
            "INSERT INTO repos (name, source) VALUES (%s, %s) RETURNING *",
            ["graphless", str(FIXTURE_REPO.resolve())])).fetchone()
    try:
        stats = await index_repo(repo["id"], embedder=indexed_repo[2])
        assert stats["graph"] == {"error": "graphify exited with 1: boom"} and stats["chunks"] > 0
        repo_ctx = ToolContext(repo={**repo, "stats": stats}, embedder=indexed_repo[2], reranker=NoopReranker())
        out = await run_tool(repo_ctx, "related", {"symbol": "ItemService"})
        assert "no code graph (building it failed: graphify exited with 1: boom)" in out.text
        assert not has_graph(repo_ctx.repo) and "architecture" not in {t["name"] for t in tools_for(False)}
    finally:
        async with connection() as conn:
            await conn.execute("DELETE FROM repos WHERE id = %s", [repo["id"]])


@pytest.mark.integration
async def test_agent_is_offered_graph_tools_only_with_a_graph(ctx, monkeypatch):
    offered: list[tuple[str, set[str]]] = []

    def fake_model(settings, system, client=None, tools=None):
        offered.append((system[0]["text"], {t["name"] for t in tools or []}))
        return ScriptedModel([[{"type": "text", "text": "ok"}]])

    monkeypatch.setattr(answer, "ClaudeModel", fake_model)
    graphless = {**ctx.repo, "stats": {k: v for k, v in ctx.repo["stats"].items() if k != "graph"}}
    for repo, graph_tools in ((ctx.repo, None), (ctx.repo, False), (graphless, None)):
        events = [e async for e in answer_question("q", repo, graph_tools=graph_tools)]
        assert events[-1]["type"] == "done"
    (with_graph, with_tools), (forced_off, off_tools), (no_graph, no_tools) = offered
    assert {"architecture", "related", "impact"} <= with_tools and "The code graph:" in with_graph
    assert "architecture" not in off_tools and "The code graph:" not in forced_off
    assert "architecture" not in no_tools and len(no_tools) == 8
