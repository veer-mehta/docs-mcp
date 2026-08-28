import json
from dataclasses import dataclass

import asyncpg

SCHEMA_SQL = "CREATE EXTENSION IF NOT EXISTS vector"


def source_pattern(name: str | None, version: str | None) -> str | None:
    if name and version:
        return f"{name}@{version}"
    if name:
        return f"{name}@%"
    if version:
        return f"%@{version}"
    return None

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {table} (
    id BIGSERIAL PRIMARY KEY,
    source_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    content TEXT NOT NULL,
    heading_path TEXT[] NOT NULL DEFAULT '{}',
    chunk_index INT NOT NULL,
    provider TEXT NOT NULL,
    embedding vector({dim}) NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, url, chunk_index)
)
"""

UPSERT_SQL = """
INSERT INTO {table} (source_id, url, title, content, heading_path, chunk_index, provider, embedding, metadata, content_hash)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8::vector, $9::jsonb, $10)
ON CONFLICT (source_id, url, chunk_index) DO UPDATE SET
    title = EXCLUDED.title,
    content = EXCLUDED.content,
    heading_path = EXCLUDED.heading_path,
    provider = EXCLUDED.provider,
    embedding = EXCLUDED.embedding,
    metadata = EXCLUDED.metadata,
    content_hash = EXCLUDED.content_hash,
    created_at = now()
"""


@dataclass
class SearchHit:
    url: str
    title: str | None
    heading_path: list[str]
    content: str
    source_id: str = ""
    similarity: float | None = None
    bm25_score: float | None = None


def to_vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in vector) + "]"


class Database:
    def __init__(self, dsn: str, table: str = "documents") -> None:
        self._dsn = dsn
        self._table = table
        self._pool: asyncpg.Pool | None = None

    async def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4, statement_cache_size=0)
        return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def ensure_schema(self, dim: int) -> None:
        pool = await self.pool()
        table = self._table
        async with pool.acquire() as conn:
            await conn.execute(SCHEMA_SQL)
            await conn.execute(
                TABLE_SQL.replace("{table}", table).replace("{dim}", str(dim))
            )
            await conn.execute(
                f"CREATE INDEX IF NOT EXISTS {table}_embedding_hnsw "
                f"ON {table} USING hnsw (embedding vector_cosine_ops)"
            )
            await conn.execute(
                f"CREATE INDEX IF NOT EXISTS {table}_source_idx ON {table} (source_id)"
            )
            await conn.execute(
                f"CREATE INDEX IF NOT EXISTS {table}_fts_idx "
                f"ON {table} USING gin (to_tsvector('english', content))"
            )
            await conn.execute(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS content_hash TEXT"
            )
            actual = await conn.fetchval(
                "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
                f"WHERE a.attrelid = '{table}'::regclass AND a.attname = 'embedding'"
            )
            expected = f"vector({dim})"
            if actual != expected:
                raise RuntimeError(
                    f"{table}.embedding column is {actual}, expected {expected}; "
                    f"DROP TABLE {table} and re-ingest"
                )

    async def upsert_chunks(self, rows: list[dict]) -> None:
        if not rows:
            return
        pool = await self.pool()
        params = [
            (
                row["source_id"],
                row["url"],
                row.get("title"),
                row["content"],
                row.get("heading_path") or [],
                row["chunk_index"],
                row["provider"],
                to_vector_literal(row["embedding"]),
                json.dumps(row.get("metadata") or {}),
                row.get("content_hash"),
            )
            for row in rows
        ]
        async with pool.acquire() as conn:
            await conn.executemany(UPSERT_SQL.replace("{table}", self._table), params)

    async def get_source_hashes(self, source_id: str) -> dict[str, str | None]:
        pool = await self.pool()
        rows = await pool.fetch(
            f"SELECT DISTINCT ON (url) url, content_hash FROM {self._table} "
            f"WHERE source_id = $1 ORDER BY url, chunk_index",
            source_id,
        )
        return {row["url"]: row["content_hash"] for row in rows}

    async def delete_stale_pages(self, source_id: str, keep_urls: set[str]) -> int:
        if not keep_urls:
            return 0
        pool = await self.pool()
        status = await pool.execute(
            f"DELETE FROM {self._table} WHERE source_id = $1 AND NOT (url = ANY($2))",
            source_id,
            list(keep_urls),
        )
        return int(status.split()[-1])

    async def _vector_rows(
        self, query_vector: list[float], pattern: str | None, n: int
    ) -> list[asyncpg.Record]:
        pool = await self.pool()
        return await pool.fetch(
            f"SELECT id, url, title, heading_path, content, source_id, "
            f"1 - (embedding <=> $1::vector) AS similarity "
            f"FROM {self._table} WHERE ($2::text IS NULL OR source_id LIKE $2) "
            f"ORDER BY embedding <=> $1::vector LIMIT $3",
            to_vector_literal(query_vector),
            pattern,
            n,
        )

    async def _keyword_rows(
        self, query_text: str, pattern: str | None, n: int
    ) -> list[asyncpg.Record]:
        pool = await self.pool()
        terms = [t for t in query_text.split() if len(t) > 2]
        if len(terms) < 2:
            return []
        websearch_query = " OR ".join(terms)
        return await pool.fetch(
            f"SELECT id, url, title, heading_path, content, source_id, "
            f"ts_rank_cd(to_tsvector('english', content), "
            f"websearch_to_tsquery('english', $1), 32) AS bm25_score "
            f"FROM {self._table} "
            f"WHERE ($2::text IS NULL OR source_id LIKE $2) "
            f"AND to_tsvector('english', content) "
            f"@@ websearch_to_tsquery('english', $1) "
            f"AND ts_rank_cd(to_tsvector('english', content), "
            f"websearch_to_tsquery('english', $1), 32) > 0.01 "
            f"ORDER BY bm25_score DESC LIMIT $3",
            websearch_query,
            pattern,
            n,
        )

    @staticmethod
    def _hit(row: asyncpg.Record) -> SearchHit:
        return SearchHit(
            url=row["url"],
            title=row["title"],
            heading_path=list(row["heading_path"]),
            content=row["content"],
            source_id=row.get("source_id", ""),
            similarity=float(row["similarity"]) if "similarity" in row.keys() else None,
            bm25_score=float(row["bm25_score"]) if "bm25_score" in row.keys() else None,
        )

    async def search(
        self,
        query_vector: list[float] | None = None,
        *,
        query_text: str | None = None,
        pattern: str | None = None,
        k: int = 5,
        mode: str = "hybrid",
        min_similarity: float = -1.0,
    ) -> list[SearchHit]:
        if mode not in ("hybrid", "vector", "keyword"):
            raise ValueError(f"unknown search mode: {mode}")
        if mode == "vector":
            if query_vector is None:
                raise ValueError("mode 'vector' requires an embedding")
            rows = await self._vector_rows(query_vector, pattern, k)
            return [self._hit(row) for row in rows]
        if mode == "keyword":
            if not query_text:
                raise ValueError("mode 'keyword' requires query text")
            rows = await self._keyword_rows(query_text, pattern, k)
            return [self._hit(row) for row in rows]

        vector_rows: list[asyncpg.Record] = []
        keyword_rows: list[asyncpg.Record] = []
        n = max(k * 3, 20)
        if query_vector is not None:
            vector_rows = await self._vector_rows(query_vector, pattern, n)
        if query_text:
            keyword_rows = await self._keyword_rows(query_text, pattern, n)
        if not vector_rows and not keyword_rows:
            return []

        entries: dict[int, tuple[float, float | None, float | None, asyncpg.Record]] = {}
        rrf_k = 60
        for rows in (vector_rows, keyword_rows):
            for position, row in enumerate(rows):
                contribution = 1.0 / (rrf_k + position + 1)
                existing = entries.get(row["id"])
                if existing is None:
                    fused, sim, bm25, rep = 0.0, None, None, row
                else:
                    fused, sim, bm25, rep = existing
                if "similarity" in row.keys():
                    sim = float(row["similarity"])
                if "bm25_score" in row.keys():
                    bm25 = float(row["bm25_score"])
                entries[row["id"]] = (fused + contribution, sim, bm25, rep)

        ranked = sorted(entries.values(), key=lambda e: e[0], reverse=True)[:k]
        return [
            SearchHit(
                url=rep["url"],
                title=rep["title"],
                heading_path=list(rep["heading_path"]),
                content=rep["content"],
                source_id=rep.get("source_id", ""),
                similarity=sim,
                bm25_score=bm25,
            )
            for _, sim, bm25, rep in ranked
            if sim is None or sim >= min_similarity
        ]

    async def list_sources(self) -> list[dict]:
        pool = await self.pool()
        rows = await pool.fetch(
            f"SELECT source_id, count(DISTINCT url) AS pages, count(*) AS chunks, "
            f"max(created_at) AS updated_at "
            f"FROM {self._table} GROUP BY source_id ORDER BY source_id"
        )
        return [dict(row) for row in rows]

    async def delete_source(self, source_id: str) -> int:
        pool = await self.pool()
        status = await pool.execute(
            f"DELETE FROM {self._table} WHERE source_id = $1", source_id
        )
        return int(status.split()[-1])

    async def drop_table(self) -> None:
        pool = await self.pool()
        await pool.execute(f"DROP TABLE IF EXISTS {self._table}")


