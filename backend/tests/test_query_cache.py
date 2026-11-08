from types import SimpleNamespace

from app.config import Settings
from app.retrieval.embeddings import QueryCache, VoyageEmbedder, get_embedder


class FakeVoyage:
    """Stands in for voyageai.AsyncClient and records each embed call."""

    def __init__(self):
        self.calls: list[tuple[tuple[str, ...], str, str]] = []

    async def embed(self, texts, model, input_type, output_dimension):
        self.calls.append((tuple(texts), model, input_type))
        return SimpleNamespace(embeddings=[[float(len(t)), 0.5, 0.25, 1.0][:output_dimension] for t in texts])


def _embedder(cache: QueryCache | None, api: FakeVoyage, model: str = "voyage-code-3", dim: int = 4):
    embedder = VoyageEmbedder("test-key", model, dim, cache)
    embedder.client = api
    return embedder


async def test_a_repeated_query_skips_the_api_across_requests():
    cache, api = QueryCache(8), FakeVoyage()
    first = await _embedder(cache, api).embed_query("where is the owner saved")
    # Each request builds its own embedder; the cache is what they share.
    second = await _embedder(cache, api).embed_query("where is the owner saved")
    assert second == first
    assert len(api.calls) == 1
    assert (cache.hits, cache.misses) == (1, 1)


async def test_the_key_is_model_dimension_and_exact_text():
    cache, api = QueryCache(8), FakeVoyage()
    await _embedder(cache, api).embed_query("owner")
    await _embedder(cache, api, model="voyage-3.5").embed_query("owner")
    await _embedder(cache, api, dim=2).embed_query("owner")
    await _embedder(cache, api).embed_query("Owner")
    assert len(api.calls) == 4
    assert cache.hits == 0


async def test_the_least_recently_used_query_is_dropped():
    cache, api = QueryCache(2), FakeVoyage()
    embedder = _embedder(cache, api)
    for query in ("a", "b", "a", "c"):  # reading "a" again makes "b" the oldest
        await embedder.embed_query(query)
    assert len(cache) == 2
    await embedder.embed_query("a")
    await embedder.embed_query("b")
    assert [call[0][0] for call in api.calls] == ["a", "b", "c", "b"]


async def test_documents_are_not_cached_and_the_cache_can_be_off():
    api = FakeVoyage()
    embedder = _embedder(QueryCache(8), api)
    await embedder.embed_documents(["chunk"])
    await embedder.embed_documents(["chunk"])
    uncached = _embedder(None, api)
    await uncached.embed_query("q")
    await uncached.embed_query("q")
    assert len(api.calls) == 4


def test_every_embedder_in_the_process_shares_one_cache():
    settings = Settings(voyage_api_key="test-key", query_cache_size=16)
    assert get_embedder(settings).cache is get_embedder(settings).cache is not None
    assert get_embedder(Settings(voyage_api_key="test-key", query_cache_size=0)).cache is None
