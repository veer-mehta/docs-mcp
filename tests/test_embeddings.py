import asyncio
import math

from tests.fakes import HashEmbeddingProvider


def test_hash_provider_deterministic_and_normalized():
    provider = HashEmbeddingProvider()
    v1, v2 = asyncio.run(provider.embed(["hello world", "hello world"]))
    v3 = asyncio.run(provider.embed(["different"]))[0]
    assert v1 == v2
    assert v1 != v3
    assert len(v1) == provider.dimensions
    norm = math.sqrt(sum(x * x for x in v1))
    assert abs(norm - 1.0) < 1e-6


def test_provider_is_built_once(monkeypatch):
    from docs_mcp import embeddings
    from docs_mcp.config import settings

    monkeypatch.setattr(settings, "embedding_provider", "api")
    monkeypatch.setattr(settings, "embedding_api_key", "key")
    monkeypatch.setattr(settings, "embedding_model", "model")
    embeddings.get_embedding_provider.cache_clear()
    try:
        assert embeddings.get_embedding_provider() is embeddings.get_embedding_provider()
    finally:
        embeddings.get_embedding_provider.cache_clear()
