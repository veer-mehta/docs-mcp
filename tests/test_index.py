import pytest

from docs_mcp.index import DocsIndex
from tests.fakes import HashEmbeddingProvider, InMemoryStore


class RecordingStore(InMemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict] = []

    async def search(self, query_vector, **kwargs):
        self.calls.append({"vector": query_vector, **kwargs})
        return []


async def test_search_rejects_unknown_mode():
    index = DocsIndex(RecordingStore(), HashEmbeddingProvider())
    with pytest.raises(ValueError, match="unknown mode"):
        await index.search("routing", mode="bad")


async def test_search_clamps_k_and_builds_source_pattern():
    store = RecordingStore()
    await DocsIndex(store, HashEmbeddingProvider()).search("routing", name="react", k=500)
    assert store.calls[0]["k"] == 20
    assert store.calls[0]["pattern"] == "react@%"
    assert len(store.calls[0]["vector"]) == HashEmbeddingProvider.dimensions


async def test_keyword_search_skips_embedding():
    store = RecordingStore()
    await DocsIndex(store, HashEmbeddingProvider()).search("routing", mode="keyword")
    assert store.calls[0]["vector"] is None
