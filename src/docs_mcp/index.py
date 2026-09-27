import asyncio

from docs_mcp import pipeline
from docs_mcp.config import settings
from docs_mcp.embeddings import get_embedding_provider
from docs_mcp.pipeline import IngestResult
from docs_mcp.storage.db import Database, SearchHit, source_pattern

SEARCH_MODES = ("hybrid", "vector", "keyword")


class DocsIndex:
    def __init__(self, store: Database, embedder) -> None:
        self.store = store
        self.embedder = embedder

    @classmethod
    async def open(cls, dsn: str | None = None) -> "DocsIndex":
        index = cls(Database(dsn or settings.database_url), get_embedding_provider())
        await index.store.ensure_schema(index.embedder.dimensions)
        return index

    async def close(self) -> None:
        await self.store.close()

    async def search(self, query: str, *, name: str | None = None, version: str | None = None, k: int = 5, mode: str = "hybrid", min_similarity: float = -1.0) -> list[SearchHit]:
        if mode not in SEARCH_MODES:
            raise ValueError(f"unknown mode: {mode} (use {'|'.join(SEARCH_MODES)})")
        vector = None if mode == "keyword" else (await self.embedder.embed([query]))[0]
        return await self.store.search(vector, query_text=query, pattern=source_pattern(name, version), k=max(1, min(k, 20)), mode=mode, min_similarity=min_similarity)

    async def ingest_site(self, name: str, version: str, base_url: str, **kwargs) -> IngestResult:
        return await pipeline.ingest_documentation(self.store, self.embedder, name, version, base_url, **kwargs)

    async def ingest_files(self, name: str, files: list[tuple[str, bytes]]) -> IngestResult:
        return await pipeline.ingest_files(self.store, self.embedder, name, files)

    async def ingest_folder(self, name: str, folder_path: str, recursive: bool = True) -> IngestResult:
        return await pipeline.ingest_folder(self.store, self.embedder, name, folder_path, recursive)

    async def sources(self) -> list[dict]:
        return await self.store.list_sources()

    async def delete(self, source_id: str) -> int:
        return await self.store.delete_source(source_id)


_shared: DocsIndex | None = None
_shared_lock = asyncio.Lock()


async def shared_index() -> DocsIndex:
    global _shared
    async with _shared_lock:
        if _shared is None:
            _shared = await DocsIndex.open()
    return _shared
