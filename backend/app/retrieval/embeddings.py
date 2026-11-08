"""Embedding and reranking providers.

Voyage's code models are used when VOYAGE_API_KEY is set. Without a key, a deterministic hashing
embedder keeps the whole pipeline runnable offline (tests, first run); its vectors only capture
shared identifiers, so semantic recall comes from Voyage.
"""

import hashlib
import math
from collections import Counter, OrderedDict
from functools import lru_cache
from typing import Protocol

import numpy as np
import voyageai
from opentelemetry import trace

from app.config import Settings
from app.retrieval.tokenize import terms


class Embedder(Protocol):
    model: str
    dim: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class QueryCache:
    """Recently embedded search queries, least recently used dropped first. Keyed by (model, dimension,
    exact text): a model returns the same vector for the same text, so entries never go stale.
    Vectors are kept as float32 (4 KB each at 1,024 dimensions); search casts to float32 anyway."""

    def __init__(self, size: int):
        self.size = size
        self.hits = 0
        self.misses = 0
        self._vectors: OrderedDict[tuple[str, int, str], np.ndarray] = OrderedDict()

    def get(self, key: tuple[str, int, str]) -> list[float] | None:
        vector = self._vectors.get(key)
        if vector is None:
            self.misses += 1
            return None
        self._vectors.move_to_end(key)
        self.hits += 1
        return vector.tolist()

    def put(self, key: tuple[str, int, str], vector: list[float]) -> None:
        self._vectors[key] = np.asarray(vector, dtype=np.float32)
        self._vectors.move_to_end(key)
        while len(self._vectors) > self.size:
            self._vectors.popitem(last=False)

    def __len__(self) -> int:
        return len(self._vectors)


@lru_cache(maxsize=None)
def query_cache(size: int) -> QueryCache:
    """One cache per process, shared by every request: each request builds its own embedder."""
    return QueryCache(size)


class VoyageEmbedder:
    def __init__(self, api_key: str, model: str, dim: int, cache: QueryCache | None = None):
        self.client = voyageai.AsyncClient(api_key=api_key, max_retries=3)
        self.model = model
        self.dim = dim
        self.cache = cache

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        result = await self.client.embed(texts, model=self.model, input_type="document", output_dimension=self.dim)
        return result.embeddings

    async def embed_query(self, text: str) -> list[float]:
        """Cached, so a repeated query skips the Voyage call: single-shot RAG searches with the user's
        question, and the same questions come back (asked again, evaluation repetitions)."""
        key = (self.model, self.dim, text)
        cached = self.cache.get(key) if self.cache is not None else None
        trace.get_current_span().set_attribute("codedoc.cache_hit", cached is not None)
        if cached is not None:
            return cached
        result = await self.client.embed([text], model=self.model, input_type="query", output_dimension=self.dim)
        vector = result.embeddings[0]
        if self.cache is not None:
            self.cache.put(key, vector)
        return vector


class HashEmbedder:
    """Feature hashing over code-aware terms and term bigrams, L2-normalised."""

    model = "hash-v1"

    def __init__(self, dim: int):
        self.dim = dim

    def _vector(self, text: str) -> list[float]:
        words = terms(text)
        features = Counter(words)
        features.update(f"{a} {b}" for a, b in zip(words, words[1:]))
        vec = [0.0] * self.dim
        for feature, count in features.items():
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[index] += sign * (1.0 + math.log(count))
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class Reranker(Protocol):
    async def rerank(self, query: str, documents: list[str], top_k: int) -> list[tuple[int, float]]: ...


class VoyageReranker:
    def __init__(self, api_key: str, model: str):
        self.client = voyageai.AsyncClient(api_key=api_key, max_retries=3)
        self.model = model

    async def rerank(self, query: str, documents: list[str], top_k: int) -> list[tuple[int, float]]:
        result = await self.client.rerank(query, documents, model=self.model, top_k=top_k)
        return [(r.index, r.relevance_score) for r in result.results]


class NoopReranker:
    async def rerank(self, query: str, documents: list[str], top_k: int) -> list[tuple[int, float]]:
        return [(i, 0.0) for i in range(min(top_k, len(documents)))]


def get_embedder(settings: Settings) -> Embedder:
    if settings.voyage_api_key:
        cache = query_cache(settings.query_cache_size) if settings.query_cache_size > 0 else None
        return VoyageEmbedder(settings.voyage_api_key, settings.embed_model, settings.embed_dim, cache)
    return HashEmbedder(settings.embed_dim)  # computed locally in microseconds; nothing to cache


def get_reranker(settings: Settings) -> Reranker:
    if settings.voyage_api_key:
        return VoyageReranker(settings.voyage_api_key, settings.rerank_model)
    return NoopReranker()
