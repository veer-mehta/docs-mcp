import math

import pytest

from docs_mcp.storage.db import Database

pytestmark = pytest.mark.integration

DSN = "postgresql://docs_mcp:docs_mcp@localhost:5432/docs_mcp"
TABLE = "documents_test"


class FakeProvider:
    name = "fake:test"
    dimensions = 1536

    async def embed(self, texts: list[str]) -> list[list[float]]:
        results = []
        for text in texts:
            seed = sum(ord(char) for char in text)
            values = [math.sin(seed + i) for i in range(self.dimensions)]
            norm = math.sqrt(sum(v * v for v in values)) or 1.0
            results.append([v / norm for v in values])
        return results


@pytest.fixture()
async def db():
    database = Database(DSN, table=TABLE)
    yield database
    await database.drop_table()
    await database.close()


async def test_upsert_and_search_roundtrip(db):
    provider = FakeProvider()
    await db.ensure_schema(provider.dimensions)

    contents = [f"chunk {i} about routing" for i in range(3)]
    vectors = await provider.embed(contents)
    rows = [
        {
            "source_id": "fake-fw@1.0",
            "url": "https://fake.dev/docs/guide",
            "title": "Guide",
            "content": content,
            "heading_path": ["Guide", f"S{i}"],
            "chunk_index": i,
            "provider": provider.name,
            "embedding": vector,
            "metadata": {},
        }
        for i, (content, vector) in enumerate(zip(contents, vectors))
    ]
    await db.upsert_chunks(rows)

    hits = await db.search((await provider.embed(["chunk 1 about routing"]))[0], k=3)
    assert hits, "expected at least one hit"
    assert hits[0].url == "https://fake.dev/docs/guide"
    assert hits[0].heading_path == ["Guide", "S1"]
    assert 0.99 < hits[0].similarity <= 1.0001

    sources = {s["source_id"]: s for s in await db.list_sources()}
    assert sources["fake-fw@1.0"]["chunks"] == 3
    assert sources["fake-fw@1.0"]["pages"] == 1


async def test_upsert_is_idempotent(db):
    provider = FakeProvider()
    await db.ensure_schema(provider.dimensions)

    row = {
        "source_id": "fake-fw@1.0",
        "url": "https://fake.dev/docs/api",
        "title": "API",
        "content": "stable content",
        "heading_path": ["API"],
        "chunk_index": 0,
        "provider": provider.name,
        "embedding": (await provider.embed(["stable content"]))[0],
        "metadata": {},
    }
    await db.upsert_chunks([row])
    row["title"] = "API v2"
    await db.upsert_chunks([row])

    sources = {s["source_id"]: s for s in await db.list_sources()}
    assert sources["fake-fw@1.0"]["chunks"] == 1


async def test_search_respects_source_filter(db):
    provider = FakeProvider()
    await db.ensure_schema(provider.dimensions)
    embedding = (await provider.embed(["unique filterable text"]))[0]
    row = {
        "source_id": "fake-fw@1.0",
        "url": "https://fake.dev/docs/only",
        "title": None,
        "content": "unique filterable text",
        "heading_path": [],
        "chunk_index": 0,
        "provider": provider.name,
        "embedding": embedding,
        "metadata": {},
    }
    await db.upsert_chunks([row])

    hits = await db.search(embedding, pattern="fake-fw@%", k=5)
    assert any(hit.url == "https://fake.dev/docs/only" for hit in hits)

    hits = await db.search(embedding, pattern="no-such-source@%", k=5)
    assert hits == []

    deleted = await db.delete_source("fake-fw@1.0")
    assert deleted >= 1


async def test_keyword_mode_ranks_lexical_matches(db):
    provider = FakeProvider()
    await db.ensure_schema(provider.dimensions)
    rows = []
    for i, content in enumerate(["The router handles URL routing and middleware.", "Rust macros generate fast token parsers.", "Unrelated paragraph about gardening tools."]):
        rows.append(
            {
                "source_id": "fake-fw@1.0",
                "url": f"https://fake.dev/docs/p{i}",
                "title": None,
                "content": content,
                "heading_path": [],
                "chunk_index": i,
                "provider": provider.name,
                "embedding": (await provider.embed([content]))[0],
                "metadata": {},
            }
        )
    await db.upsert_chunks(rows)

    hits = await db.search(query_text="rust token", mode="keyword", k=3)
    assert hits, "keyword search should match lexical content"
    assert "Rust macros" in hits[0].content
    for hit in hits:
        assert hit.similarity is None
        assert hit.bm25_score is not None

    empty = await db.search(query_text="zzzqqq", mode="keyword", k=3)
    assert empty == []


async def test_hybrid_mode_fuses_and_dedupes(db):
    provider = FakeProvider()
    await db.ensure_schema(provider.dimensions)
    contents = ["Pydantic models validate JSON payloads strictly.", "Loose prose mentioning pydantic occasionally."]
    rows = []
    for i, content in enumerate(contents):
        rows.append(
            {
                "source_id": "fake-fw@1.0",
                "url": f"https://fake.dev/docs/h{i}",
                "title": None,
                "content": content,
                "heading_path": [],
                "chunk_index": i,
                "provider": provider.name,
                "embedding": (await provider.embed([content]))[0],
                "metadata": {},
            }
        )
    await db.upsert_chunks(rows)

    query = "pydantic validate"
    vector = (await provider.embed([query]))[0]
    hybrid = await db.search(vector, query_text=query, mode="hybrid", k=5)
    urls = [hit.url for hit in hybrid]
    assert len(urls) == len(set(urls)), "fusion must dedupe documents"

    vector_only = {hit.url for hit in await db.search(vector, mode="vector", k=5)}
    keyword_only = {hit.url for hit in await db.search(query_text=query, mode="keyword", k=5)}
    assert set(urls) >= (vector_only & keyword_only)

    both_legs = vector_only & keyword_only
    assert both_legs, "fixture should have documents matching both retrieval legs"
    for hit in hybrid:
        if hit.url in both_legs:
            assert hit.similarity is not None, "dual-leg hit must keep vector score"
            assert hit.bm25_score is not None, "dual-leg hit must keep keyword score"


async def test_invalid_mode_and_missing_inputs(db):
    with pytest.raises(ValueError):
        await db.search(mode="bogus")
    with pytest.raises(ValueError):
        await db.search(mode="vector")
    with pytest.raises(ValueError):
        await db.search(mode="keyword")


async def _seed_two_pages(db, provider):
    embedding = (await provider.embed(["seed"]))[0]
    rows = []
    for page, n_chunks in (("a", 2), ("b", 1)):
        for i in range(n_chunks):
            rows.append(
                {
                    "source_id": "fake-fw@1.0",
                    "url": f"https://fake.dev/{page}",
                    "title": None,
                    "content": f"chunk {page}{i} seed text for hashing",
                    "heading_path": [],
                    "chunk_index": i,
                    "provider": provider.name,
                    "embedding": embedding,
                    "metadata": {},
                    "content_hash": f"hash-{page}",
                }
            )
    await db.upsert_chunks(rows)


async def test_get_source_hashes_returns_page_level_map(db):
    provider = FakeProvider()
    await db.ensure_schema(provider.dimensions)
    await _seed_two_pages(db, provider)

    hashes = await db.get_source_hashes("fake-fw@1.0")
    assert hashes == {"https://fake.dev/a": "hash-a", "https://fake.dev/b": "hash-b"}
    assert await db.get_source_hashes("missing@9") == {}


async def test_delete_stale_pages_keeps_visited_urls(db):
    provider = FakeProvider()
    await db.ensure_schema(provider.dimensions)
    await _seed_two_pages(db, provider)

    removed = await db.delete_stale_pages("fake-fw@1.0", {"https://fake.dev/a"})
    assert removed == 1
    hashes = await db.get_source_hashes("fake-fw@1.0")
    assert list(hashes) == ["https://fake.dev/a"]

    assert await db.delete_stale_pages("other@1", set()) == 0
