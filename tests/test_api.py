import httpx
import pytest

from docs_mcp import api
from docs_mcp.index import DocsIndex, shared_index
from docs_mcp.jobs import JobRegistry
from docs_mcp.pipeline import IngestResult
from tests.fakes import HashEmbeddingProvider, InMemoryStore


@pytest.fixture()
def store():
    return InMemoryStore()


@pytest.fixture()
async def client(store):
    api.app.dependency_overrides[shared_index] = lambda: DocsIndex(store, HashEmbeddingProvider())
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as value:
        yield value
    api.app.dependency_overrides.clear()


def test_openapi_exposes_api_contract():
    schema = api.app.openapi()
    assert schema["info"]["title"] == "fathom-mcp"
    assert set(schema["paths"]) == {"/", "/about", "/search", "/sources", "/sources/{source_id}", "/ingest", "/upload", "/upload-folder", "/ingest-deps", "/jobs", "/jobs/{job_id}", "/llm-chat"}


async def test_validation_errors_keep_bad_request_status(client):
    response = await client.post("/ingest", json={})
    assert response.status_code == 400
    assert response.json() == {"error": "invalid request"}

    response = await client.get("/search")
    assert response.status_code == 400
    assert response.json() == {"error": "query param 'q' is required"}

    response = await client.get("/search", params={"q": "routing", "mode": "bad"})
    assert response.status_code == 400
    assert "unknown mode" in response.json()["error"]


async def test_ingest_body_is_validated_and_forwarded(client, monkeypatch):
    captured = {}

    async def fake_ingest_or_submit(_index, **kwargs):
        captured.update(kwargs)
        return IngestResult("fw@1.0", 1, 1, 2, 0)

    monkeypatch.setattr(api, "ingest_or_submit", fake_ingest_or_submit)
    response = await client.post("/ingest", json={"name": "fw", "version": "1.0", "base_url": "https://fw.dev"})

    assert response.status_code == 200
    assert response.json()["chunks_indexed"] == 2
    assert captured == {"name": "fw", "version": "1.0", "base_url": "https://fw.dev", "background": False, "max_depth": None, "max_pages": None, "prune_missing": False, "lang": "", "sitemap": False}


async def test_background_ingest_returns_accepted_with_poll_url(client, monkeypatch):
    job = JobRegistry().create(name="fw", version="1.0", base_url="https://fw.dev", max_depth=None, max_pages=None)

    async def fake_ingest_or_submit(_index, **kwargs):
        return job

    monkeypatch.setattr(api, "ingest_or_submit", fake_ingest_or_submit)
    response = await client.post("/ingest", json={"name": "fw", "version": "1.0", "base_url": "https://fw.dev", "background": True})

    assert response.status_code == 202
    assert response.json() == {"job_id": job.id, "status": "queued", "poll": f"/jobs/{job.id}"}


async def test_missing_uploads_keep_existing_errors(client):
    response = await client.post("/upload", data={"name": "docs"})
    assert response.status_code == 400
    assert response.json() == {"error": "no files provided"}

    response = await client.post("/ingest-deps", data={"max_deps": "20"})
    assert response.status_code == 400
    assert response.json() == {"error": "no file provided"}


async def test_multipart_uploads_are_parsed(client, store, monkeypatch):
    async def fake_resolve_dependencies(filename, content, max_deps=20):
        return {"filename": filename, "content": content, "max_deps": max_deps}

    monkeypatch.setattr(api, "resolve_dependencies", fake_resolve_dependencies)

    response = await client.post("/upload", data={"name": "docs"}, files=[("files", ("guide.md", b"# Guide\n\nHow routing works.", "text/markdown"))])
    assert response.status_code == 200
    assert response.json()["source_id"] == "docs@latest"
    assert {(row["source_id"], row["url"]) for row in store.rows} == {("docs@latest", "file:///guide.md")}

    response = await client.post("/ingest-deps", data={"max_deps": "5"}, files={"file": ("requirements.txt", b"fastapi\n", "text/plain")})
    assert response.status_code == 200
    assert response.json() == {"filename": "requirements.txt", "content": "fastapi\n", "max_deps": 5}


async def test_unknown_job_returns_not_found(client):
    response = await client.get("/jobs/unknown")
    assert response.status_code == 404
    assert response.json() == {"error": "unknown job: unknown"}
