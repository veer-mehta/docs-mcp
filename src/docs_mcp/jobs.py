import asyncio
import logging
import uuid
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone

from docs_mcp.pipeline import IngestResult, ingest_documentation
from docs_mcp.storage.db import Database

logger = logging.getLogger(__name__)

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

    def create(
        self,
        *,
        name: str,
        version: str,
        base_url: str,
        max_depth: int | None,
        max_pages: int | None,
        prune_missing: bool = False,
        lang: str = "",
        sitemap: bool = False,
    ) -> Job:
        job = Job(
            id=uuid.uuid4().hex[:8],
            name=name, version=version, base_url=base_url,
            max_depth=max_depth, max_pages=max_pages, prune_missing=prune_missing,
            lang=lang, sitemap=sitemap,
        )
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
    db: Database,
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
    _runner=None,
) -> Job:
    async def _run(job: Job) -> None:
        runner = _runner
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
            if runner is not None:
                job.result = await runner(
                    name=job.name, version=job.version,
                    base_url=job.base_url, max_depth=job.max_depth,
                    max_pages=job.max_pages, prune_missing=job.prune_missing,
                    on_progress=on_progress,
                )
            else:
                job.result = await ingest_documentation(
                    db, name=job.name, version=job.version,
                    base_url=job.base_url, max_depth=job.max_depth,
                    max_pages=job.max_pages, prune_missing=job.prune_missing,
                    lang=job.lang, sitemap=job.sitemap,
                    on_progress=on_progress,
                )
            job.status = "done"
        except Exception as exc:
            logger.exception("ingest job %s failed", job.id)
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.finished_at = datetime.now(timezone.utc)

    job = registry.create(
        name=name, version=version, base_url=base_url,
        max_depth=max_depth, max_pages=max_pages, prune_missing=prune_missing,
        lang=lang, sitemap=sitemap,
    )
    job._task = asyncio.get_running_loop().create_task(_run(job))
    return job
