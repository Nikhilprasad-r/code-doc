"""The local embeddings client (LOCAL_EMBED_URL), against a fake OpenAI-style server: no MLX needed."""

import json

import httpx
import numpy as np
import pytest

from app.config import Settings
from app.retrieval.embeddings import HashEmbedder, LocalEmbedder, VoyageEmbedder, get_embedder

MODEL = "Qwen/Qwen3-Embedding-0.6B"


def fake_server(served_model: str = MODEL, size: int = 6):
    """Answers /v1/embeddings with a vector of `size` dimensions per input, in reverse order (the
    `index` field says where each belongs), and records each request body."""
    requests: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        data = [{"object": "embedding", "index": i, "embedding": [float(len(text))] + [1.0] * (size - 1)}
                for i, text in enumerate(body["input"])]
        return httpx.Response(200, json={"object": "list", "model": served_model, "data": data[::-1]})

    return httpx.MockTransport(handle), requests


async def test_queries_get_the_instruction_and_documents_go_in_as_they_are():
    transport, requests = fake_server()
    embedder = LocalEmbedder("http://mac:8090/", MODEL, dim=4, transport=transport)
    await embedder.embed_documents(["class Owner {}", "def save(): ..."])
    await embedder.embed_query("where are owners saved?")
    assert requests[0] == {"model": MODEL, "input": ["class Owner {}", "def save(): ..."]}
    assert requests[1]["input"] == [LocalEmbedder.QUERY_INSTRUCTION + "where are owners saved?"]
    assert requests[1]["input"][0].startswith("Instruct: ") and "\nQuery: " in requests[1]["input"][0]


async def test_vectors_come_back_in_order_cut_to_the_index_size_and_unit_length():
    transport, _ = fake_server(size=6)
    vectors = await LocalEmbedder("http://mac:8090", MODEL, dim=4, transport=transport).embed_documents(["a", "bbb"])
    assert [len(v) for v in vectors] == [4, 4]
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0)
    first, second = (np.array(v) for v in vectors)
    assert np.allclose(first, np.array([1, 1, 1, 1]) / 2)  # "a": first value 1, from the server's index 0
    assert np.allclose(second, np.array([3, 1, 1, 1]) / np.sqrt(12))  # "bbb": first value 3


async def test_a_server_running_another_model_or_smaller_vectors_is_refused():
    transport, _ = fake_server(served_model="BAAI/bge-small-en")
    with pytest.raises(RuntimeError, match="serves BAAI/bge-small-en, not Qwen/Qwen3-Embedding-0.6B"):
        await LocalEmbedder("http://mac:8090", MODEL, dim=4, transport=transport).embed_query("q")
    transport, _ = fake_server(size=3)
    with pytest.raises(RuntimeError, match="at least 4 dimensions"):
        await LocalEmbedder("http://mac:8090", MODEL, dim=4, transport=transport).embed_query("q")


def test_a_local_server_takes_precedence_and_counts_as_real_embeddings():
    local = Settings(local_embed_url="http://host.docker.internal:8090", voyage_api_key="key")
    assert isinstance(get_embedder(local), LocalEmbedder) and get_embedder(local).model == MODEL
    assert not local.offline_embeddings
    assert isinstance(get_embedder(Settings(local_embed_url=None, voyage_api_key="key")), VoyageEmbedder)
    offline = Settings(local_embed_url=None, voyage_api_key=None)
    assert isinstance(get_embedder(offline), HashEmbedder) and offline.offline_embeddings
