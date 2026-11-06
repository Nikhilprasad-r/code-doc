"""End-to-end indexing and search against a real Postgres (docker compose up -d db)."""

import pytest

from app.db import connection
from app.ingest.pipeline import index_repo
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
