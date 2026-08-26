import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from docs_mcp.pipeline import IngestResult, ingest_documentation
from docs_mcp.storage.db import Database

logger = logging.getLogger(__name__)

MAX_JOB_HISTORY = 100


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Job:
    id: str
    name: str
    version: str
    base_url: str
    max_depth: int | None
    max_pages: int | None
    prune_missing: bool = False
    status: str = "queued"
    created_at: datetime = field(default_factory=_now)
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
        payload = {
            "id": self.id,
            "source_id": self.source_id,
            "name": self.name,
            "version": self.version,
            "base_url": self.base_url,
            "max_depth": self.max_depth,
            "max_pages": self.max_pages,
            "prune_missing": self.prune_missing,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": (
                self.finished_at.isoformat() if self.finished_at else None
            ),
            "pages_crawled": self.pages_crawled,
            "pages_indexed": self.pages_indexed,
            "pages_unchanged": self.pages_unchanged,
            "pages_removed": self.pages_removed,
            "chunks_indexed": self.chunks_indexed,
            "errors": self.errors,
            "error": self.error,
        }
        if self.result is not None:
            payload["result"] = self.result
        return payload

    async def wait_done(self) -> None:
        if self._task is not None:
            await self._task


class JobRegistry:
    def __init__(self, capacity: int = MAX_JOB_HISTORY):
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
    ) -> Job:
        job = Job(
            id=uuid.uuid4().hex[:8],
            name=name,
            version=version,
            base_url=base_url,
            max_depth=max_depth,
            max_pages=max_pages,
            prune_missing=prune_missing,
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


async def _run_job(job: Job, runner) -> None:
    job.status = "running"
    job.started_at = _now()

    def on_progress(result: IngestResult) -> None:
        job.pages_crawled = result.pages_crawled
        job.pages_indexed = result.pages_indexed
        job.chunks_indexed = result.chunks_indexed
        job.errors = result.errors
        job.pages_unchanged = result.pages_unchanged
        job.pages_removed = result.pages_removed

    try:
        job.result = await runner(
            name=job.name,
            version=job.version,
            base_url=job.base_url,
            max_depth=job.max_depth,
            max_pages=job.max_pages,
            prune_missing=job.prune_missing,
            on_progress=on_progress,
        )
        job.status = "done"
    except Exception as exc:
        logger.exception("ingest job %s failed", job.id)
        job.status = "failed"
        job.error = f"{type(exc).__name__}: {exc}"
    finally:
        job.finished_at = _now()


def submit_ingest(
    db: Database,
    *,
    name: str,
    version: str,
    base_url: str,
    max_depth: int | None = None,
    max_pages: int | None = None,
    prune_missing: bool = False,
    registry: JobRegistry = JOBS,
    runner=None,
) -> Job:
    if runner is None:
        async def _default_runner(**kwargs):
            return await ingest_documentation(db, **kwargs)
        runner = _default_runner

    job = registry.create(
        name=name,
        version=version,
        base_url=base_url,
        max_depth=max_depth,
        max_pages=max_pages,
        prune_missing=prune_missing,
    )
    job._task = asyncio.get_running_loop().create_task(_run_job(job, runner))
    return job
