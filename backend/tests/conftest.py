from pathlib import Path

import psycopg
import pytest

from app.config import Settings, get_settings
from app.db import close_db, connection, init_db
from app.ingest.pipeline import FileAnalysis, analyze_tree, index_repo
from app.retrieval.embeddings import HashEmbedder

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "sample_repo"


@pytest.fixture(scope="session")
def analyzed() -> dict[str, FileAnalysis]:
    files = analyze_tree(FIXTURE_REPO, Settings(chunk_max_chars=400))
    return {f.path: f for f in files}


def _postgres_available() -> bool:
    try:
        psycopg.connect(get_settings().database_url, connect_timeout=2).close()
        return True
    except psycopg.OperationalError:
        return False


@pytest.fixture(scope="session")
async def indexed_repo():
    """The fixture repo indexed into Postgres with the offline embedder; removed afterwards."""
    if not _postgres_available():
        pytest.skip("Postgres not running (docker compose up -d db)")
    await init_db()
    async with connection() as conn:
        row = await (await conn.execute(
            "INSERT INTO repos (name, source) VALUES (%s, %s) RETURNING id",
            ["sample-test", str(FIXTURE_REPO.resolve())],
        )).fetchone()
    repo_id = row["id"]
    embedder = HashEmbedder(get_settings().embed_dim)
    stats = await index_repo(repo_id, embedder=embedder)
    yield repo_id, stats, embedder
    async with connection() as conn:
        await conn.execute("DELETE FROM repos WHERE id = %s", [repo_id])
    await close_db()
