"""Embedding and reranking providers.

Voyage's code models are used when VOYAGE_API_KEY is set. Without a key, a deterministic hashing
embedder keeps the whole pipeline runnable offline (tests, first run); its vectors only capture
shared identifiers, so semantic recall comes from Voyage.
"""

import hashlib
import math
from collections import Counter
from typing import Protocol

import voyageai

from app.config import Settings
from app.retrieval.tokenize import terms


class Embedder(Protocol):
    model: str
    dim: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class VoyageEmbedder:
    def __init__(self, api_key: str, model: str, dim: int):
        self.client = voyageai.AsyncClient(api_key=api_key, max_retries=3)
        self.model = model
        self.dim = dim

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        result = await self.client.embed(texts, model=self.model, input_type="document", output_dimension=self.dim)
        return result.embeddings

    async def embed_query(self, text: str) -> list[float]:
        result = await self.client.embed([text], model=self.model, input_type="query", output_dimension=self.dim)
        return result.embeddings[0]


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
        return VoyageEmbedder(settings.voyage_api_key, settings.embed_model, settings.embed_dim)
    return HashEmbedder(settings.embed_dim)


def get_reranker(settings: Settings) -> Reranker:
    if settings.voyage_api_key:
        return VoyageReranker(settings.voyage_api_key, settings.rerank_model)
    return NoopReranker()
