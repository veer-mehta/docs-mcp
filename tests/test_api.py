import httpx
import pytest

from docs_mcp import api
from docs_mcp.pipeline import IngestResult


@pytest.fixture()
async def client():
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as value:
        yield value


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

    async def fake_ingest_or_submit(_db, **kwargs):
        captured.update(kwargs)
        return {"status": "ok"}, 200

    monkeypatch.setattr(api, "ingest_or_submit", fake_ingest_or_submit)
    response = await client.post("/ingest", json={"name": "fw", "version": "1.0", "base_url": "https://fw.dev"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert captured == {"name": "fw", "version": "1.0", "base_url": "https://fw.dev", "background": False, "max_depth": None, "max_pages": None, "prune_missing": False, "lang": "", "sitemap": False}


async def test_missing_uploads_keep_existing_errors(client):
    response = await client.post("/upload", data={"name": "docs"})
    assert response.status_code == 400
    assert response.json() == {"error": "no files provided"}

    response = await client.post("/ingest-deps", data={"max_deps": "20"})
    assert response.status_code == 400
    assert response.json() == {"error": "no file provided"}


async def test_multipart_uploads_are_parsed(client, monkeypatch):
    captured = {}

    async def fake_ingest_files(_db, *, name, files):
        captured.update(name=name, files=files)
        return IngestResult("docs@latest", 1, 1, 1, 0)

    async def fake_resolve_dependencies(filename, content, max_deps=20):
        return {"filename": filename, "content": content, "max_deps": max_deps}

    monkeypatch.setattr(api, "ingest_files", fake_ingest_files)
    monkeypatch.setattr(api, "resolve_dependencies", fake_resolve_dependencies)

    response = await client.post("/upload", data={"name": "docs"}, files=[("files", ("guide.md", b"# Guide", "text/markdown"))])
    assert response.status_code == 200
    assert captured == {"name": "docs", "files": [("guide.md", b"# Guide")]}

    response = await client.post("/ingest-deps", data={"max_deps": "5"}, files={"file": ("requirements.txt", b"fastapi\n", "text/plain")})
    assert response.status_code == 200
    assert response.json() == {"filename": "requirements.txt", "content": "fastapi\n", "max_deps": 5}


async def test_unknown_job_returns_not_found(client):
    response = await client.get("/jobs/unknown")
    assert response.status_code == 404
    assert response.json() == {"error": "unknown job: unknown"}
