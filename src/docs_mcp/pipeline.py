import asyncio
import hashlib
import json
import logging
import sys
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass

from docs_mcp.config import settings
from docs_mcp.embeddings import get_embedding_provider
from docs_mcp.processing.chunker import chunk_markdown
from docs_mcp.processing.extract import html_to_markdown
from docs_mcp.storage.db import Database

logger = logging.getLogger(__name__)

EMBED_BATCH_SIZE = 32


@dataclass
class IngestResult:
    source_id: str
    pages_crawled: int
    pages_indexed: int
    chunks_indexed: int
    errors: int
    pages_unchanged: int = 0
    pages_removed: int = 0


def content_hash(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


async def ingest_documentation(
    db: Database,
    name: str,
    version: str,
    base_url: str,
    max_depth: int | None = None,
    max_pages: int | None = None,
    on_progress: Callable[[IngestResult], None] | None = None,
    prune_missing: bool = False,
) -> IngestResult:
    source_id = f"{name}@{version}"
    provider = get_embedding_provider()
    await db.ensure_schema(provider.dimensions)
    known_hashes = await db.get_source_hashes(source_id)
    seen_urls: set[str] = set()

    def report() -> None:
        if on_progress is not None:
            on_progress(result)

    with tempfile.TemporaryFile(mode="w+b") as stderr_file:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "docs_mcp.scraper.runner",
            "--url",
            base_url,
            "--depth",
            str(max_depth if max_depth is not None else settings.crawl_max_depth),
            "--max-pages",
            str(max_pages if max_pages is not None else settings.crawl_max_pages),
            "--cache-dir",
            settings.crawl_cache_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=stderr_file,
            limit=64 * 1024 * 1024,
        )

        result = IngestResult(
            source_id=source_id,
            pages_crawled=0,
            pages_indexed=0,
            chunks_indexed=0,
            errors=0,
        )
        report()
        pending: list[dict] = []

        async def flush() -> None:
            nonlocal pending
            if not pending:
                return
            vectors = await provider.embed([row["content"] for row in pending])
            for row, vector in zip(pending, vectors):
                row["embedding"] = vector
            await db.upsert_chunks(pending)
            result.chunks_indexed += len(pending)
            pending = []
            report()

        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                page = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("skipping malformed crawler output line")
                result.errors += 1
                continue

            result.pages_crawled += 1
            markdown = html_to_markdown(page.get("html") or "", page.get("url") or "")
            if not markdown:
                result.errors += 1
                continue
            url = page["url"]
            seen_urls.add(url)
            page_hash = content_hash(markdown)
            if known_hashes.get(url) == page_hash:
                result.pages_unchanged += 1
                report()
                continue
            chunks = chunk_markdown(markdown)
            for index, chunk in enumerate(chunks):
                pending.append(
                    {
                        "source_id": source_id,
                        "url": url,
                        "title": page.get("title"),
                        "content": chunk.content,
                        "heading_path": chunk.heading_path,
                        "chunk_index": index,
                        "provider": provider.name,
                        "metadata": {},
                        "content_hash": page_hash,
                    }
                )
            result.pages_indexed += 1
            report()
            if len(pending) >= EMBED_BATCH_SIZE:
                await flush()

        await flush()
        report()
        return_code = await proc.wait()
        if return_code != 0:
            result.errors += 1
            stderr_file.seek(0)
            tail = stderr_file.read().decode(errors="replace")[-2000:]
            logger.error("crawler exited with %s: %s", return_code, tail)
        elif prune_missing and seen_urls:
            # Only prune on a clean crawl that saw at least one page; callers
            # should leave caps high enough to cover the whole site.
            result.pages_removed = await db.delete_stale_pages(source_id, seen_urls)

    return asdict(result)
