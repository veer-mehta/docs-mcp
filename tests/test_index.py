import asyncio
import math
from datetime import datetime, timezone

import pytest

from docs_mcp.index import DocsIndex, IngestResult, JobRegistry, submit_ingest
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


def test_registry_create_get_list():
    reg = JobRegistry()
    job = reg.create(name="fw", version="1.0", base_url="https://fw.dev/docs", max_depth=2, max_pages=10)
    assert reg.get(job.id) is job
    assert job.status == "queued"
    assert job.source_id == "fw@1.0"
    assert [j.id for j in reg.list()] == [job.id]
    assert reg.get("nope") is None


def test_registry_prunes_oldest_finished_when_full():
    reg = JobRegistry(capacity=3)
    jobs = [reg.create(name=f"fw{i}", version="1", base_url="https://x.dev", max_depth=None, max_pages=None) for i in range(3)]
    for i, job in enumerate(jobs):
        job.status = "done"
        job.finished_at = datetime.now(timezone.utc)
    extra = reg.create(name="overflow", version="1", base_url="https://x.dev", max_depth=None, max_pages=None)
    ids = {job.id for job in reg._jobs.values()}
    assert jobs[0].id not in ids
    assert extra.id in ids
    assert jobs[2].id in ids


async def test_submit_ingest_lifecycle_done():
    seen_kwargs = {}

    class FakeResult:
        pages_crawled = 4
        pages_indexed = 3
        chunks_indexed = 11
        errors = 0
        pages_unchanged = 1
        pages_removed = 0

    class FakeIndex:
        async def ingest_site(self, **kwargs):
            seen_kwargs.update(kwargs)
            kwargs["on_progress"](FakeResult())
            return IngestResult("x@1", pages_crawled=4, pages_indexed=3, chunks_indexed=11, errors=0)

    reg = JobRegistry()
    job = submit_ingest(FakeIndex(), name="x", version="1", base_url="https://x.dev", max_depth=1, max_pages=5, lang="en", sitemap=True, registry=reg)
    assert job.status in ("queued", "running")
    await job.wait_done()

    assert job.status == "done"
    assert job.error is None
    assert job.started_at <= job.finished_at
    assert job.pages_crawled == 4
    assert job.chunks_indexed == 11
    assert seen_kwargs["name"] == "x"
    assert seen_kwargs["max_depth"] == 1
    assert seen_kwargs["lang"] == "en"
    assert seen_kwargs["sitemap"] is True
    payload = job.to_dict()
    assert payload["status"] == "done"
    assert payload["result"]["chunks_indexed"] == 11
    assert payload["result"] not in (None, {})


async def test_submit_ingest_failure_captured():
    class ExplodingIndex:
        async def ingest_site(self, **kwargs):
            raise RuntimeError("crawl exploded")

    reg = JobRegistry()
    job = submit_ingest(ExplodingIndex(), name="y", version="2", base_url="https://y.dev", max_depth=None, max_pages=None, registry=reg)
    await job.wait_done()

    assert job.status == "failed"
    assert "crawl exploded" in job.error
    assert job.finished_at is not None


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
    from docs_mcp import embeddings, settings

    monkeypatch.setattr(settings, "embedding_provider", "api")
    monkeypatch.setattr(settings, "embedding_api_key", "key")
    monkeypatch.setattr(settings, "embedding_model", "model")
    embeddings.get_embedding_provider.cache_clear()
    try:
        assert embeddings.get_embedding_provider() is embeddings.get_embedding_provider()
    finally:
        embeddings.get_embedding_provider.cache_clear()
