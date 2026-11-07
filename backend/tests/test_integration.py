"""End-to-end indexing and search against a real Postgres (docker compose up -d db)."""

import httpx
import pytest

from app.db import connection
from app.ingest.pipeline import index_repo
from app.main import app
from app.retrieval.embeddings import NoopReranker
from app.retrieval.search import hybrid_search

pytestmark = pytest.mark.integration


async def test_index_stats(indexed_repo):
    _, stats, _ = indexed_repo
    assert stats["files"] == 10
    assert stats["endpoints"] >= 15
    assert stats["dependencies"] >= 10
    assert stats["symbols"] >= 20
    assert stats["embed_model"] == "hash-v1"


async def test_structured_tables_populated(indexed_repo):
    repo_id, _, _ = indexed_repo
    async with connection() as conn:
        endpoint = await (await conn.execute(
            "SELECT path, line FROM endpoints WHERE repo_id = %s AND handler = 'showOwner'", [repo_id],
        )).fetchone()
        symbol = await (await conn.execute(
            "SELECT path FROM symbols WHERE repo_id = %s AND qualified = 'ItemService.archive'", [repo_id],
        )).fetchone()
    assert endpoint["path"].endswith("OwnerController.java")
    assert symbol["path"] == "api/routes/items.py"


@pytest.mark.parametrize("query, expected_path", [
    ("ItemService archive", "api/routes/items.py"),
    ("where is the owner creation form handled", "svc/src/main/java/com/example/petclinic/OwnerController.java"),
    ("listOrders", "web/src/server.ts"),
    ("gin router health", "gosvc/main.go"),
])
async def test_hybrid_search_finds_the_right_file(indexed_repo, query, expected_path):
    repo_id, stats, embedder = indexed_repo
    hits = await hybrid_search(repo_id, query, embedder, NoopReranker(),
                               repo_embed_model=stats["embed_model"], k=3)
    assert hits, query
    assert expected_path in [h.path for h in hits], [h.path for h in hits]


async def test_path_prefix_filter(indexed_repo):
    repo_id, stats, embedder = indexed_repo
    hits = await hybrid_search(repo_id, "routes", embedder, NoopReranker(),
                               repo_embed_model=stats["embed_model"], k=5, path_prefix="web/")
    assert hits and all(h.path.startswith("web/") for h in hits)


async def test_reindex_reuses_cached_embeddings(indexed_repo):
    repo_id, first, embedder = indexed_repo
    second = await index_repo(repo_id, embedder=embedder)
    assert second["chunks"] == first["chunks"]
    assert second["embedding_cache_hits"] == second["chunks"]


async def test_search_modes_isolate_each_retriever(indexed_repo):
    repo_id, stats, embedder = indexed_repo
    common = dict(repo_embed_model=stats["embed_model"], k=5)
    keyword = await hybrid_search(repo_id, "ItemService archive", embedder, NoopReranker(), mode="keyword", **common)
    vector = await hybrid_search(repo_id, "ItemService archive", embedder, NoopReranker(), mode="vector", **common)
    assert keyword and all(h.matched_by == ["keyword"] for h in keyword)
    assert vector and all(h.matched_by == ["vector"] for h in vector)
    with pytest.raises(ValueError):
        await hybrid_search(repo_id, "x", embedder, NoopReranker(), mode="bm42", **common)


async def test_failed_reindex_before_writing_keeps_serving_previous_index(indexed_repo):
    async with connection() as conn:
        row = await (await conn.execute(
            "INSERT INTO repos (name, source, status, stats) VALUES (%s, %s, 'queued', %s) RETURNING id",
            ["gone", "/nonexistent/path", '{"files": 3}'])).fetchone()
    try:
        with pytest.raises(Exception):
            await index_repo(row["id"])
        async with connection() as conn:
            repo = await (await conn.execute("SELECT status, error FROM repos WHERE id = %s", [row["id"]])).fetchone()
        assert repo["status"] == "ready"
        assert repo["error"].startswith("Re-index failed, still serving the previous index")
    finally:
        async with connection() as conn:
            await conn.execute("DELETE FROM repos WHERE id = %s", [row["id"]])


async def test_file_endpoint_resolves_shortened_paths(indexed_repo):
    """Models sometimes cite `items.py` or `routes/items.py`; a unique suffix match opens the full path."""
    repo_id, _, _ = indexed_repo
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        async def fetch(path: str) -> httpx.Response:
            return await client.get(f"/repos/{repo_id}/file", params={"path": path})

        exact = await fetch("api/routes/items.py")
        assert exact.status_code == 200 and exact.json()["path"] == "api/routes/items.py"
        for shortened in ("items.py", "routes/items.py", "./api/routes/items.py"):
            response = await fetch(shortened)
            assert response.status_code == 200 and response.json()["path"] == "api/routes/items.py", shortened
        assert (await fetch("tems.py")).status_code == 404  # a suffix must start at a path segment
        assert (await fetch("nope.py")).status_code == 404

        async with connection() as conn:
            await conn.execute(
                "INSERT INTO files (repo_id, path, language, content, content_hash, line_count) "
                "VALUES (%s, 'other/items.py', 'python', 'x = 1', 'h', 1)", [repo_id])
        try:
            ambiguous = await fetch("items.py")
            assert ambiguous.status_code == 404 and "Several files match items.py" in ambiguous.json()["detail"]
        finally:
            async with connection() as conn:
                await conn.execute("DELETE FROM files WHERE repo_id = %s AND path = 'other/items.py'", [repo_id])
