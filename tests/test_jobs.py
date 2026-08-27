from datetime import datetime, timezone

from docs_mcp.jobs import JobRegistry, submit_ingest


def test_registry_create_get_list():
    reg = JobRegistry()
    job = reg.create(
        name="fw", version="1.0", base_url="https://fw.dev/docs",
        max_depth=2, max_pages=10,
    )
    assert reg.get(job.id) is job
    assert job.status == "queued"
    assert job.source_id == "fw@1.0"
    assert [j.id for j in reg.list()] == [job.id]
    assert reg.get("nope") is None


def test_registry_prunes_oldest_finished_when_full():
    reg = JobRegistry(capacity=3)
    jobs = [
        reg.create(name=f"fw{i}", version="1", base_url="https://x.dev",
                   max_depth=None, max_pages=None)
        for i in range(3)
    ]
    for i, job in enumerate(jobs):
        job.status = "done"
        pass
        job.finished_at = datetime.now(timezone.utc)
    extra = reg.create(name="overflow", version="1", base_url="https://x.dev",
                       max_depth=None, max_pages=None)
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

    async def fake_runner(**kwargs):
        seen_kwargs.update(kwargs)
        kwargs["on_progress"](FakeResult())
        return {
            "source_id": "x@1", "pages_crawled": 4, "pages_indexed": 3,
            "chunks_indexed": 11, "errors": 0,
        }

    reg = JobRegistry()
    job = submit_ingest(
        None, name="x", version="1", base_url="https://x.dev",
        max_depth=1, max_pages=5, registry=reg, _runner=fake_runner,
    )
    assert job.status in ("queued", "running")
    await job.wait_done()

    assert job.status == "done"
    assert job.error is None
    assert job.started_at <= job.finished_at
    assert job.pages_crawled == 4
    assert job.chunks_indexed == 11
    assert seen_kwargs["name"] == "x"
    assert seen_kwargs["max_depth"] == 1
    payload = job.to_dict()
    assert payload["status"] == "done"
    assert payload["result"]["chunks_indexed"] == 11
    assert payload["result"] not in (None, {})


async def test_submit_ingest_failure_captured():
    async def boom(**kwargs):
        raise RuntimeError("crawl exploded")

    reg = JobRegistry()
    job = submit_ingest(
        None, name="y", version="2", base_url="https://y.dev",
        max_depth=None, max_pages=None, registry=reg, _runner=boom,
    )
    await job.wait_done()

    assert job.status == "failed"
    assert "crawl exploded" in job.error
    assert job.finished_at is not None
