"""Hybrid retrieval: keyword (Postgres full-text over code-aware terms) + vector (pgvector),
fused with Reciprocal Rank Fusion, then reranked by a cross-encoder when one is configured."""

from dataclasses import dataclass

import numpy as np

from app.db import connection
from app.observability.tracing import tracer
from app.retrieval.embeddings import Embedder, Reranker
from app.retrieval.tokenize import build_tsquery

RRF_K = 60
CANDIDATES = 50


@dataclass
class SearchHit:
    chunk_id: int
    path: str
    start_line: int
    end_line: int
    scope: str | None
    header: str
    content: str
    score: float
    matched_by: list[str]


_COLUMNS = "id, path, start_line, end_line, scope, header, content"


def _path_filter(path_prefix: str | None) -> tuple[str, list[object]]:
    if not path_prefix:
        return "", []
    return " AND path LIKE %s", [path_prefix.rstrip("/") + "%"]


async def keyword_candidates(repo_id: int, query: str, path_prefix: str | None = None,
                             limit: int = CANDIDATES) -> list[dict]:
    tsquery = build_tsquery(query)
    if tsquery is None:
        return []
    where, params = _path_filter(path_prefix)
    sql = (f"SELECT {_COLUMNS}, ts_rank_cd(tsv, q) AS rank FROM chunks, to_tsquery('simple', %s) q "
           f"WHERE repo_id = %s AND tsv @@ q{where} ORDER BY rank DESC LIMIT %s")
    async with connection() as conn:
        return await (await conn.execute(sql, [tsquery, repo_id, *params, limit])).fetchall()


async def vector_candidates(repo_id: int, query_vector: list[float], path_prefix: str | None = None,
                            limit: int = CANDIDATES) -> list[dict]:
    where, params = _path_filter(path_prefix)
    sql = (f"SELECT {_COLUMNS}, embedding <=> %s AS distance FROM chunks "
           f"WHERE repo_id = %s AND embedding IS NOT NULL{where} ORDER BY distance LIMIT %s")
    vector = np.asarray(query_vector, dtype=np.float32)
    async with connection() as conn, conn.transaction():
        # Keep scanning the HNSW index until enough rows pass the repo filter (pgvector >= 0.8).
        await conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        await conn.execute("SET LOCAL hnsw.ef_search = 100")
        return await (await conn.execute(sql, [vector, repo_id, *params, limit])).fetchall()


def reciprocal_rank_fusion(*ranked_lists: tuple[str, list[dict]]) -> list[tuple[dict, float, list[str]]]:
    scores: dict[int, float] = {}
    rows: dict[int, dict] = {}
    sources: dict[int, list[str]] = {}
    for name, rows_in_rank_order in ranked_lists:
        for rank, row in enumerate(rows_in_rank_order):
            scores[row["id"]] = scores.get(row["id"], 0.0) + 1.0 / (RRF_K + rank + 1)
            rows[row["id"]] = row
            sources.setdefault(row["id"], []).append(name)
    ordered = sorted(scores, key=scores.__getitem__, reverse=True)
    return [(rows[i], scores[i], sources[i]) for i in ordered]


async def hybrid_search(
    repo_id: int,
    query: str,
    embedder: Embedder,
    reranker: Reranker,
    *,
    repo_embed_model: str | None,
    k: int = 8,
    path_prefix: str | None = None,
    mode: str = "hybrid",
    rerank: bool = True,
) -> list[SearchHit]:
    """`mode` is "hybrid" (default), "keyword" or "vector"; the single-source modes and `rerank=False`
    exist so the evaluation can measure what each stage contributes."""
    if mode not in {"hybrid", "keyword", "vector"}:
        raise ValueError(f"unknown search mode {mode!r}")
    lists: list[tuple[str, list[dict]]] = []
    if mode in {"hybrid", "keyword"}:
        with tracer.start_as_current_span("retrieval.keyword") as span:
            keyword = await keyword_candidates(repo_id, query, path_prefix)
            span.set_attribute("codedoc.candidates", len(keyword))
        lists.append(("keyword", keyword))
    # Vectors from a different model live in a different space; skip rather than return noise.
    if mode in {"hybrid", "vector"} and repo_embed_model == embedder.model:
        with tracer.start_as_current_span("retrieval.embed_query", attributes={"gen_ai.request.model": embedder.model}):
            query_vector = await embedder.embed_query(query)
        with tracer.start_as_current_span("retrieval.vector") as span:
            vector = await vector_candidates(repo_id, query_vector, path_prefix)
            span.set_attribute("codedoc.candidates", len(vector))
        lists.append(("vector", vector))

    fused = reciprocal_rank_fusion(*lists)[: max(k * 3, 20)]
    if not fused:
        return []
    if rerank:
        with tracer.start_as_current_span("retrieval.rerank", attributes={"codedoc.candidates": len(fused)}):
            order = await reranker.rerank(query, [f"{r['header']}\n\n{r['content']}" for r, _, _ in fused], top_k=k)
    else:
        order = [(i, 0.0) for i in range(min(k, len(fused)))]
    hits = []
    for index, rerank_score in order:
        row, fused_score, matched_by = fused[index]
        hits.append(SearchHit(
            chunk_id=row["id"], path=row["path"], start_line=row["start_line"], end_line=row["end_line"],
            scope=row["scope"], header=row["header"], content=row["content"],
            score=rerank_score or fused_score, matched_by=matched_by,
        ))
    return hits
