"""Postgres access: one database holds every index (vectors, full-text, symbols, structured facts)."""

from contextlib import asynccontextmanager

from pgvector.psycopg import register_vector_async
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import get_settings

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS repos (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    source      TEXT NOT NULL,
    ref         TEXT,
    commit_sha  TEXT,
    status      TEXT NOT NULL DEFAULT 'queued',   -- queued | indexing | ready | failed
    progress    JSONB NOT NULL DEFAULT '{{}}',
    stats       JSONB NOT NULL DEFAULT '{{}}',
    error       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS files (
    id           BIGSERIAL PRIMARY KEY,
    repo_id      INT NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    path         TEXT NOT NULL,
    language     TEXT,
    content      TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    line_count   INT NOT NULL,
    UNIQUE (repo_id, path)
);

CREATE TABLE IF NOT EXISTS chunks (
    id           BIGSERIAL PRIMARY KEY,
    repo_id      INT NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    path         TEXT NOT NULL,
    language     TEXT,
    start_line   INT NOT NULL,
    end_line     INT NOT NULL,
    scope        TEXT,
    header       TEXT NOT NULL,
    content      TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    search_text  TEXT NOT NULL,
    tsv          TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', search_text)) STORED,
    embedding    VECTOR({dim})
);
CREATE INDEX IF NOT EXISTS chunks_repo_idx ON chunks (repo_id);
CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING GIN (tsv);
CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS symbols (
    id         BIGSERIAL PRIMARY KEY,
    repo_id    INT NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    path       TEXT NOT NULL,
    name       TEXT NOT NULL,
    qualified  TEXT NOT NULL,
    kind       TEXT NOT NULL,
    start_line INT NOT NULL,
    end_line   INT NOT NULL,
    signature  TEXT
);
CREATE INDEX IF NOT EXISTS symbols_name_idx ON symbols (repo_id, lower(name));

CREATE TABLE IF NOT EXISTS imports (
    repo_id INT NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    path    TEXT NOT NULL,
    module  TEXT NOT NULL,
    line    INT NOT NULL
);
CREATE INDEX IF NOT EXISTS imports_repo_idx ON imports (repo_id, module);

CREATE TABLE IF NOT EXISTS endpoints (
    repo_id   INT NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    framework TEXT NOT NULL,
    method    TEXT NOT NULL,
    route     TEXT NOT NULL,
    handler   TEXT,
    path      TEXT NOT NULL,
    line      INT NOT NULL
);

CREATE TABLE IF NOT EXISTS dependencies (
    repo_id       INT NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    ecosystem     TEXT NOT NULL,
    name          TEXT NOT NULL,
    version_spec  TEXT,
    scope         TEXT,
    manifest_path TEXT NOT NULL
);

-- Embeddings keyed by content hash survive re-indexing, so unchanged chunks are never re-embedded.
CREATE TABLE IF NOT EXISTS embedding_cache (
    content_hash TEXT NOT NULL,
    model        TEXT NOT NULL,
    embedding    VECTOR({dim}) NOT NULL,
    PRIMARY KEY (content_hash, model)
);

-- 'ast' (syntax-aware) or 'lines' (fixed windows; only used as the naive baseline in evaluation)
ALTER TABLE repos ADD COLUMN IF NOT EXISTS chunking TEXT NOT NULL DEFAULT 'ast';

-- Code graph from graphify (app/ingest/graph.py). Node ids are graphify's, or 'external:<name>'.
CREATE TABLE IF NOT EXISTS graph_nodes (
    repo_id    INT NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    node_id    TEXT NOT NULL,
    label      TEXT NOT NULL,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL,
    path       TEXT,
    start_line INT,
    end_line   INT,
    community  INT,
    degree     INT NOT NULL DEFAULT 0,
    PRIMARY KEY (repo_id, node_id)
);
CREATE INDEX IF NOT EXISTS graph_nodes_name_idx ON graph_nodes (repo_id, lower(name));
CREATE INDEX IF NOT EXISTS graph_nodes_label_idx ON graph_nodes (repo_id, lower(label));

CREATE TABLE IF NOT EXISTS graph_edges (
    repo_id  INT NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    source   TEXT NOT NULL,
    target   TEXT NOT NULL,
    relation TEXT NOT NULL,
    inferred BOOLEAN NOT NULL DEFAULT false,
    path     TEXT,
    line     INT
);
CREATE INDEX IF NOT EXISTS graph_edges_source_idx ON graph_edges (repo_id, source);
CREATE INDEX IF NOT EXISTS graph_edges_target_idx ON graph_edges (repo_id, target);

CREATE TABLE IF NOT EXISTS graph_communities (
    repo_id   INT NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    community INT NOT NULL,
    label     TEXT NOT NULL,
    size      INT NOT NULL,
    directory TEXT,
    top_nodes JSONB NOT NULL DEFAULT '[]',
    links     JSONB NOT NULL DEFAULT '[]',
    PRIMARY KEY (repo_id, community)
);

CREATE TABLE IF NOT EXISTS conversations (
    id         UUID PRIMARY KEY,
    repo_id    INT NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    messages   JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_pool: AsyncConnectionPool | None = None


async def _configure(conn: AsyncConnection) -> None:
    await register_vector_async(conn)


async def init_db() -> AsyncConnectionPool:
    """Create the schema (idempotent) and open the shared pool."""
    global _pool
    if _pool is not None:
        return _pool
    settings = get_settings()
    async with await AsyncConnection.connect(settings.database_url, autocommit=True) as conn:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await conn.execute(SCHEMA.format(dim=settings.embed_dim))
    _pool = AsyncConnectionPool(
        settings.database_url,
        min_size=1,
        max_size=10,
        open=False,
        configure=_configure,
        kwargs={"row_factory": dict_row},
    )
    await _pool.open()
    return _pool


async def close_db() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def connection():
    if _pool is None:
        await init_db()
    assert _pool is not None
    async with _pool.connection() as conn:
        yield conn
