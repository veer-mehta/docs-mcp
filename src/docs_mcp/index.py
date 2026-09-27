import asyncio
import hashlib
import json
import logging
import sys
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path

from docs_mcp import settings
from docs_mcp.embeddings import get_embedding_provider
from docs_mcp.store import ChunkStore, SearchHit, source_pattern
from docs_mcp.text import chunk_markdown, file_to_markdown, html_to_markdown

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


def _page_rows(provider, source_id: str, url: str, title: str | None, markdown: str, page_hash: str) -> list[dict]:
    return [
        {
            "source_id": source_id,
            "url": url,
            "title": title,
            "content": chunk.content,
            "heading_path": chunk.heading_path,
            "chunk_index": index,
            "provider": provider.name,
            "metadata": {},
            "content_hash": page_hash,
        }
        for index, chunk in enumerate(chunk_markdown(markdown))
    ]


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
    db: ChunkStore,
    provider,
    name: str,
    version: str,
    base_url: str,
    max_depth: int | None = None,
    max_pages: int | None = None,
    on_progress: Callable[[IngestResult], None] | None = None,
    prune_missing: bool = False,
    lang: str = "",
    sitemap: bool = False,
) -> IngestResult:
    source_id = f"{name}@{version}"
    known_hashes = await db.get_source_hashes(source_id)
    seen_urls: set[str] = set()

    def report() -> None:
        if on_progress is not None:
            on_progress(result)

    with tempfile.TemporaryFile(mode="w+b") as stderr_file:
        cmd = [
            sys.executable,
            "-m",
            "docs_mcp.crawler",
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
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=stderr_file, limit=64 * 1024 * 1024)

        result = IngestResult(source_id=source_id, pages_crawled=0, pages_indexed=0, chunks_indexed=0, errors=0)
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
            rows = _page_rows(provider, source_id, url, page.get("title"), markdown, page_hash)
            if not rows:
                result.errors += 1
                continue
            pending.extend(rows)
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

    return result


async def ingest_files(db: ChunkStore, provider, name: str, files: list[tuple[str, bytes]]) -> IngestResult:
    source_id = f"{name}@latest"

    result = IngestResult(source_id=source_id, pages_crawled=0, pages_indexed=0, chunks_indexed=0, errors=0)
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
            page_hash = hashlib.sha256(markdown.encode()).hexdigest()
            pending.extend(_page_rows(provider, source_id, f"file:///{filename}", filename, markdown, page_hash))
            result.pages_indexed += 1
            if len(pending) >= 8:
                await _flush_pending(provider, db, pending, result)
        finally:
            tmp_path.unlink(missing_ok=True)

    await _flush_pending(provider, db, pending, result)
    return result


SUPPORTED_EXTS = {".html", ".htm", ".md", ".txt", ".pdf"}


async def ingest_folder(db: ChunkStore, provider, name: str, folder_path: str, recursive: bool = True) -> IngestResult:
    root = Path(folder_path).expanduser().resolve()
    if not root.is_dir():
        return IngestResult(source_id=f"{name}@latest", pages_crawled=0, pages_indexed=0, chunks_indexed=0, errors=1)

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

    return await ingest_files(db, provider, name, files)


SEARCH_MODES = ("hybrid", "vector", "keyword")


class DocsIndex:
    def __init__(self, store: ChunkStore, embedder) -> None:
        self.store = store
        self.embedder = embedder

    @classmethod
    async def open(cls, dsn: str | None = None) -> "DocsIndex":
        index = cls(ChunkStore(dsn or settings.database_url), get_embedding_provider())
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
        return await ingest_documentation(self.store, self.embedder, name, version, base_url, **kwargs)

    async def ingest_files(self, name: str, files: list[tuple[str, bytes]]) -> IngestResult:
        return await ingest_files(self.store, self.embedder, name, files)

    async def ingest_folder(self, name: str, folder_path: str, recursive: bool = True) -> IngestResult:
        return await ingest_folder(self.store, self.embedder, name, folder_path, recursive)

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


MAX_JOB_HISTORY = 100


@dataclass
class Job:
    id: str
    name: str
    version: str
    base_url: str
    max_depth: int | None
    max_pages: int | None
    prune_missing: bool = False
    lang: str = ""
    sitemap: bool = False
    status: str = "queued"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    result: dict | None = None
    pages_crawled: int = 0
    pages_indexed: int = 0
    chunks_indexed: int = 0
    errors: int = 0
    pages_unchanged: int = 0
    pages_removed: int = 0
    _task: asyncio.Task | None = field(default=None, repr=False, compare=False)

    @property
    def source_id(self) -> str:
        return f"{self.name}@{self.version}"

    def to_dict(self) -> dict:
        d = {}
        for f in fields(self):
            if f.name.startswith("_"):
                continue
            v = getattr(self, f.name)
            if isinstance(v, datetime):
                v = v.isoformat()
            d[f.name] = v
        d["source_id"] = self.source_id
        return d

    async def wait_done(self) -> None:
        if self._task is not None:
            await self._task


class JobRegistry:
    def __init__(self, capacity: int = MAX_JOB_HISTORY) -> None:
        self._capacity = capacity
        self._jobs: dict[str, Job] = {}

    def create(self, *, name: str, version: str, base_url: str, max_depth: int | None, max_pages: int | None, prune_missing: bool = False, lang: str = "", sitemap: bool = False) -> Job:
        job = Job(id=uuid.uuid4().hex[:8], name=name, version=version, base_url=base_url, max_depth=max_depth, max_pages=max_pages, prune_missing=prune_missing, lang=lang, sitemap=sitemap)
        self._prune()
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)

    def _prune(self) -> None:
        if len(self._jobs) < self._capacity:
            return
        for job in self._jobs.values():
            if job.status in ("done", "failed"):
                del self._jobs[job.id]
                return


JOBS = JobRegistry()


def submit_ingest(
    index: DocsIndex,
    *,
    name: str,
    version: str,
    base_url: str,
    max_depth: int | None = None,
    max_pages: int | None = None,
    prune_missing: bool = False,
    lang: str = "",
    sitemap: bool = False,
    registry: JobRegistry = JOBS,
) -> Job:
    async def _run(job: Job) -> None:
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)

        def on_progress(result: IngestResult) -> None:
            job.pages_crawled = result.pages_crawled
            job.pages_indexed = result.pages_indexed
            job.chunks_indexed = result.chunks_indexed
            job.errors = result.errors
            job.pages_unchanged = result.pages_unchanged
            job.pages_removed = result.pages_removed

        try:
            result = await index.ingest_site(
                name=job.name,
                version=job.version,
                base_url=job.base_url,
                max_depth=job.max_depth,
                max_pages=job.max_pages,
                prune_missing=job.prune_missing,
                lang=job.lang,
                sitemap=job.sitemap,
                on_progress=on_progress,
            )
            job.result = asdict(result)
            job.status = "done"
        except Exception as exc:
            logger.exception("ingest job %s failed", job.id)
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.finished_at = datetime.now(timezone.utc)

    job = registry.create(name=name, version=version, base_url=base_url, max_depth=max_depth, max_pages=max_pages, prune_missing=prune_missing, lang=lang, sitemap=sitemap)
    job._task = asyncio.get_running_loop().create_task(_run(job))
    return job


async def ingest_or_submit(index: DocsIndex, *, name: str, version: str, base_url: str, background: bool = False, **kwargs) -> Job | IngestResult:
    if background:
        return submit_ingest(index, name=name, version=version, base_url=base_url, **kwargs)
    return await index.ingest_site(name=name, version=version, base_url=base_url, **kwargs)
