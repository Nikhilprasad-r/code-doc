"""Indexing pipeline: source -> files -> symbols, imports, endpoints, dependencies, chunks -> embeddings."""

import asyncio
import hashlib
import json
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from app.config import Settings, get_settings
from app.db import connection
from app.ingest.chunker import Chunk, chunk_code, chunk_text
from app.ingest.dependencies import Dependency, is_manifest, parse_manifest
from app.ingest.endpoints import Endpoint, extract_endpoints
from app.ingest.languages import TREE_SITTER_LANGS
from app.ingest.source import SourceError, clone, extract_zip, is_git_url, local_commit, walk
from app.ingest.symbols import Symbol, extract_imports, extract_symbols, parse
from app.retrieval.embeddings import Embedder, get_embedder
from app.retrieval.tokenize import build_search_text

log = logging.getLogger(__name__)

UPLOAD_PREFIX = "upload:"


@dataclass
class FileAnalysis:
    path: str
    language: str
    text: str
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[tuple[str, int]] = field(default_factory=list)
    endpoints: list[Endpoint] = field(default_factory=list)
    dependencies: list[Dependency] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)

    @property
    def content_hash(self) -> str:
        return sha256(self.text)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def analyze_file(path: str, text: str, language: str, max_chars: int) -> FileAnalysis:
    result = FileAnalysis(path, language, text)
    if language in TREE_SITTER_LANGS:
        try:
            tree = parse(language, text.encode("utf-8"))
            result.symbols = extract_symbols(language, tree, text.encode("utf-8"))
            result.chunks = chunk_code(path, language, text, tree, result.symbols, max_chars)
        except Exception:  # a grammar failure on one file must not fail the index
            log.warning("syntax-aware chunking failed for %s; using line chunks", path, exc_info=True)
            result.chunks = chunk_text(path, language, text, max_chars)
    else:
        result.chunks = chunk_text(path, language, text, max_chars)
    result.imports = extract_imports(language, text)
    result.endpoints = extract_endpoints(language, text)
    if is_manifest(path):
        result.dependencies = parse_manifest(path, text)
    return result


def analyze_tree(root: Path, settings: Settings) -> list[FileAnalysis]:
    return [analyze_file(path, text, language, settings.chunk_max_chars)
            for path, text, language in walk(root, settings.max_file_bytes)]


async def _set_status(repo_id: int, status: str, **fields: object) -> None:
    sets = ["status = %s", "updated_at = now()"]
    values: list[object] = [status]
    for key, value in fields.items():
        sets.append(f"{key} = %s")
        values.append(json.dumps(value) if key in {"progress", "stats"} else value)
    async with connection() as conn:
        await conn.execute(f"UPDATE repos SET {', '.join(sets)} WHERE id = %s", [*values, repo_id])


async def _resolve_source(repo: dict, settings: Settings) -> tuple[Path, str | None]:
    source: str = repo["source"]
    workdir = settings.data_dir / "repos" / str(repo["id"])
    if is_git_url(source):
        sha = await asyncio.to_thread(clone, source, workdir, repo["ref"])
        return workdir, sha
    if source.startswith(UPLOAD_PREFIX):
        archive = settings.data_dir / "uploads" / f"{repo['id']}.zip"
        root = await asyncio.to_thread(extract_zip, archive, workdir)
        return root, None
    root = Path(source).expanduser()
    if not root.is_dir():
        raise SourceError(f"Not a directory: {source}")
    return root, await asyncio.to_thread(local_commit, root)


async def _write_metadata(repo_id: int, files: list[FileAnalysis]) -> None:
    async with connection() as conn, conn.transaction():
        for table in ("chunks", "files", "symbols", "imports", "endpoints", "dependencies"):
            await conn.execute(f"DELETE FROM {table} WHERE repo_id = %s", [repo_id])
        async with conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO files (repo_id, path, language, content, content_hash, line_count) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                [(repo_id, f.path, f.language, f.text, f.content_hash, f.text.count("\n") + 1) for f in files],
            )
            await cur.executemany(
                "INSERT INTO symbols (repo_id, path, name, qualified, kind, start_line, end_line, signature) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [(repo_id, f.path, s.name, s.qualified, s.kind, s.start_line, s.end_line, s.signature)
                 for f in files for s in f.symbols],
            )
            await cur.executemany(
                "INSERT INTO imports (repo_id, path, module, line) VALUES (%s, %s, %s, %s)",
                [(repo_id, f.path, module, line) for f in files for module, line in f.imports],
            )
            await cur.executemany(
                "INSERT INTO endpoints (repo_id, framework, method, route, handler, path, line) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [(repo_id, e.framework, e.method, e.route, e.handler, f.path, e.line)
                 for f in files for e in f.endpoints],
            )
            await cur.executemany(
                "INSERT INTO dependencies (repo_id, ecosystem, name, version_spec, scope, manifest_path) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                [(repo_id, d.ecosystem, d.name, d.version_spec, d.scope, f.path)
                 for f in files for d in f.dependencies],
            )


async def _embed_with_cache(embedder: Embedder, chunks: list[Chunk]) -> tuple[list[np.ndarray], int]:
    """Embeddings for `chunks`, reusing cached vectors by content hash. Returns (vectors, cache_hits)."""
    hashes = [sha256(c.embed_text) for c in chunks]
    async with connection() as conn:
        rows = await (await conn.execute(
            "SELECT content_hash, embedding FROM embedding_cache WHERE model = %s AND content_hash = ANY(%s)",
            [embedder.model, list(set(hashes))],
        )).fetchall()
    cached = {r["content_hash"]: r["embedding"] for r in rows}
    missing = [i for i, h in enumerate(hashes) if h not in cached]
    if missing:
        vectors = await embedder.embed_documents([chunks[i].embed_text for i in missing])
        fresh = {hashes[i]: np.asarray(v, dtype=np.float32) for i, v in zip(missing, vectors)}
        async with connection() as conn, conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO embedding_cache (content_hash, model, embedding) VALUES (%s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                [(h, embedder.model, v) for h, v in fresh.items()],
            )
        cached.update(fresh)
    return [cached[h] for h in hashes], len(chunks) - len(missing)


async def _write_chunks(repo_id: int, chunks: list[Chunk], vectors: list[np.ndarray]) -> None:
    async with connection() as conn, conn.cursor() as cur:
        await cur.executemany(
            "INSERT INTO chunks (repo_id, path, language, start_line, end_line, scope, header, content, "
            "content_hash, search_text, embedding) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (repo_id, c.path, c.language, c.start_line, c.end_line, c.scope, c.header, c.content,
                 sha256(c.embed_text), build_search_text(c.path, c.header, c.content), v)
                for c, v in zip(chunks, vectors)
            ],
        )


async def index_repo(repo_id: int, settings: Settings | None = None, embedder: Embedder | None = None) -> dict:
    settings = settings or get_settings()
    embedder = embedder or get_embedder(settings)
    started = time.monotonic()
    async with connection() as conn:
        repo = await (await conn.execute("SELECT * FROM repos WHERE id = %s", [repo_id])).fetchone()
    if repo is None:
        raise ValueError(f"repo {repo_id} not found")

    try:
        await _set_status(repo_id, "indexing", progress={"phase": "fetching"}, error=None)
        root, commit_sha = await _resolve_source(repo, settings)

        await _set_status(repo_id, "indexing", progress={"phase": "parsing"}, commit_sha=commit_sha)
        files = await asyncio.to_thread(analyze_tree, root, settings)
        await _write_metadata(repo_id, files)

        chunks = [c for f in files for c in f.chunks]
        cache_hits = 0
        batch = settings.embed_batch_size
        for start in range(0, len(chunks), batch):
            await _set_status(repo_id, "indexing",
                              progress={"phase": "embedding", "done": start, "total": len(chunks)})
            part = chunks[start:start + batch]
            vectors, hits = await _embed_with_cache(embedder, part)
            cache_hits += hits
            await _write_chunks(repo_id, part, vectors)

        stats = {
            "files": len(files),
            "chunks": len(chunks),
            "symbols": sum(len(f.symbols) for f in files),
            "endpoints": sum(len(f.endpoints) for f in files),
            "dependencies": sum(len(f.dependencies) for f in files),
            "lines": sum(f.text.count("\n") + 1 for f in files),
            "languages": dict(Counter(f.language for f in files).most_common()),
            "embed_model": embedder.model,
            "embedding_cache_hits": cache_hits,
            "seconds": round(time.monotonic() - started, 1),
        }
        await _set_status(repo_id, "ready", progress={"phase": "done"}, stats=stats)
        return stats
    except Exception as exc:
        log.exception("indexing repo %s failed", repo_id)
        await _set_status(repo_id, "failed", error=str(exc)[:2000], progress={"phase": "failed"})
        raise
