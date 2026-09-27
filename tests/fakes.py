import hashlib
import math


class HashEmbeddingProvider:
    """Deterministic fake embeddings for development and testing only."""

    name = "hash"
    dimensions = 1536

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        seed = int.from_bytes(digest[:8], "big")
        values = [math.sin(seed + i) for i in range(self.dimensions)]
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]


class InMemoryStore:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def ensure_schema(self, dim: int) -> None:
        pass

    async def close(self) -> None:
        pass

    async def upsert_chunks(self, rows: list[dict]) -> None:
        self.rows.extend(dict(row) for row in rows)

    async def get_source_hashes(self, source_id: str) -> dict[str, str | None]:
        return {row["url"]: row["content_hash"] for row in self.rows if row["source_id"] == source_id}

    async def search(self, query_vector, *, query_text=None, pattern=None, k=5, mode="hybrid", min_similarity=-1.0):
        return []

    async def list_sources(self) -> list[dict]:
        return []

    async def delete_source(self, source_id: str) -> int:
        before = len(self.rows)
        self.rows = [row for row in self.rows if row["source_id"] != source_id]
        return before - len(self.rows)
