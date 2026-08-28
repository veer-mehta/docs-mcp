import asyncio
import hashlib
import json
import logging
import sys
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from docs_mcp.config import settings
from docs_mcp.embeddings import get_embedding_provider
from docs_mcp.processing.chunker import chunk_markdown
from docs_mcp.processing.extract import html_to_markdown, file_to_markdown
from docs_mcp.storage.db import Database

logger = logging.getLogger(__name__)

@dataclass
class IngestResult:
    source_id: str
    pages_crawled: int
    pages_indexed: int
    chunks_indexed: int
    errors: int
    pages_unchanged: int = 0
    pages_removed: int = 0


async def _flush_pending(provider, db, pending, result):
    if not pending:
        return
    vectors = await provider.embed([row["content"] for row in pending])
    for row, vector in zip(pending, vectors):
        row["embedding"] = vector
    await db.upsert_chunks(pending)
    result.chunks_indexed += len(pending)
    pending.clear()


async def ingest_documentation(
    db: Database,
    name: str,
    version: str,
    base_url: str,
    max_depth: int | None = None,
    max_pages: int | None = None,
    on_progress: Callable[[IngestResult], None] | None = None,
    prune_missing: bool = False,
    lang: str = "",
    sitemap: bool = False,
) -> dict:
    source_id = f"{name}@{version}"
    provider = get_embedding_provider()
    await db.ensure_schema(provider.dimensions)
    known_hashes = await db.get_source_hashes(source_id)
    seen_urls: set[str] = set()

    def report() -> None:
        if on_progress is not None:
            on_progress(result)

    with tempfile.TemporaryFile(mode="w+b") as stderr_file:
        cmd = [
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
        ]
        if lang:
            cmd.extend(["--lang", lang])
        if sitemap:
            cmd.append("--sitemap")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
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
            page_hash = hashlib.sha256(markdown.encode()).hexdigest()
            if known_hashes.get(url) == page_hash:
                result.pages_unchanged += 1
                report()
                continue
            chunks = chunk_markdown(markdown)
            if not chunks:
                result.errors += 1
                continue
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
            if len(pending) >= 8:
                await _flush_pending(provider, db, pending, result)

        await _flush_pending(provider, db, pending, result)
        report()
        return_code = await proc.wait()
        if return_code != 0:
            result.errors += 1
            stderr_file.seek(0)
            tail = stderr_file.read().decode(errors="replace")[-2000:]
            logger.error("crawler exited with %s: %s", return_code, tail)
        elif prune_missing and seen_urls:
            result.pages_removed = await db.delete_stale_pages(source_id, seen_urls)

    return asdict(result)


async def ingest_files(
    db: Database,
    name: str,
    files: list[tuple[str, bytes]],
) -> IngestResult:
    source_id = f"{name}@latest"
    provider = get_embedding_provider()
    await db.ensure_schema(provider.dimensions)

    result = IngestResult(
        source_id=source_id,
        pages_crawled=0,
        pages_indexed=0,
        chunks_indexed=0,
        errors=0,
    )
    pending: list[dict] = []

    for filename, content in files:
        result.pages_crawled += 1
        safe_suffix = Path(filename).suffix or ".tmp"
        with tempfile.NamedTemporaryFile(suffix=safe_suffix, delete=False) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)
        try:
            markdown = file_to_markdown(tmp_path, filename)
            if not markdown:
                result.errors += 1
                continue
            chunks = chunk_markdown(markdown)
            url = f"file:///{filename}"
            page_hash = hashlib.sha256(markdown.encode()).hexdigest()
            for index, chunk in enumerate(chunks):
                pending.append(
                    {
                        "source_id": source_id,
                        "url": url,
                        "title": filename,
                        "content": chunk.content,
                        "heading_path": chunk.heading_path,
                        "chunk_index": index,
                        "provider": provider.name,
                        "metadata": {},
                        "content_hash": page_hash,
                    }
                )
            result.pages_indexed += 1
            if len(pending) >= 8:
                await _flush_pending(provider, db, pending, result)
        finally:
            tmp_path.unlink(missing_ok=True)

    await _flush_pending(provider, db, pending, result)
    return result


SUPPORTED_EXTS = {".html", ".htm", ".md", ".txt", ".pdf"}


async def ingest_folder(
    db: Database,
    name: str,
    folder_path: str,
    recursive: bool = True,
) -> IngestResult:
    root = Path(folder_path).expanduser().resolve()
    if not root.is_dir():
        return IngestResult(
            source_id=f"{name}@latest",
            pages_crawled=0, pages_indexed=0, chunks_indexed=0, errors=1,
        )

    files: list[tuple[str, bytes]] = []
    seen_inodes: set[int] = set()
    iterator = root.rglob("*") if recursive else root.iterdir()
    for p in sorted(iterator):
        if p.name.startswith(".") or not p.is_file():
            continue
        try:
            inode = p.stat().st_ino
            if inode in seen_inodes:
                continue
            seen_inodes.add(inode)
        except OSError:
            continue
        if p.suffix.lower() not in SUPPORTED_EXTS:
            continue
        try:
            content = p.read_bytes()
        except OSError:
            continue
        files.append((str(p.relative_to(root)), content))

    return await ingest_files(db, name=name, files=files)
