import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, File, Form, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from docs_mcp import __version__, settings
from docs_mcp.deps import resolve_dependencies
from docs_mcp.index import JOBS, DocsIndex, Job, ingest_or_submit, shared_index

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


async def answer_question(index, query: str) -> dict:
    hits = await index.search(query, k=5)
    if not hits:
        return {"answer": "No matching documentation found.", "sources": []}
    context_lines = []
    for hit in hits:
        header = hit.title or hit.url
        if hit.heading_path:
            header += " — " + " > ".join(hit.heading_path)
        context_lines.append(f"**{header}**\n\n{hit.content}")
    context = "\n\n".join(context_lines)
    prompt = f"Answer the following question using only the provided context. Keep it concise and conversational.\n\nQuestion: {query}\n\nContext:\n{context}"
    answer = await generate_llm_response(prompt)
    sources: list[dict] = []
    seen_urls: set[str] = set()
    for hit in hits:
        if hit.url in seen_urls:
            continue
        seen_urls.add(hit.url)
        sources.append({"title": hit.title or hit.url, "url": hit.url, "heading_path": hit.heading_path, "content": hit.content})
    return {"answer": answer, "sources": sources}


async def generate_llm_response(prompt: str) -> str:
    import httpx

    api_key = settings.llm_api_key
    if not settings.llm_model:
        return "[LLM disabled: set LLM_MODEL in .env]"
    if not api_key:
        return "[LLM disabled: set LLM_API_KEY in .env]"

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": settings.llm_model, "messages": [{"role": "user", "content": prompt}], "max_tokens": settings.llm_max_tokens, "temperature": 0.7}

    base_url = settings.llm_base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=120.0) as client:
        delay = 3.0
        last_error: Exception | None = None
        for _ in range(MAX_ATTEMPTS):
            try:
                response = await client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
                if response.status_code == 429:
                    if "free-models-per-day" in response.text:
                        return "[LLM provider daily free quota exhausted — try again after the daily reset, switch provider in .env, or add credits]"
                    last_error = RuntimeError(f"LLM provider returned 429 (rate limited); retrying")
                    logger.warning("%s", last_error)
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                if response.status_code >= 500:
                    last_error = RuntimeError(f"LLM provider returned {response.status_code}; retrying")
                    logger.warning("%s", last_error)
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                response.raise_for_status()
                result = response.json()
                choice = result.get("choices", [{}])[0]
                content = choice.get("message", {}).get("content", "")
                if choice.get("finish_reason") == "length":
                    content += "\n\n[answer truncated — raise LLM_MAX_TOKENS in .env]"
                return content
            except Exception as exc:
                last_error = exc
                logger.exception("LLM request failed")
                await asyncio.sleep(delay)
                delay *= 2
        return f"[Error generating response: {last_error}]"


INDEX_HTML = Path(__file__).parent / "index.html"


@asynccontextmanager
async def lifespan(app):
    index = await shared_index()
    logger.info("embedding model ready: %s (%d dims)", index.embedder.name, index.embedder.dimensions)
    yield
    await index.close()


app = FastAPI(title="docs-mcp", version=__version__, lifespan=lifespan)


class IngestPayload(BaseModel):
    name: str
    version: str
    base_url: str
    background: bool = False
    max_depth: int | None = None
    max_pages: int | None = None
    prune_missing: bool = False
    lang: str = ""
    sitemap: bool = False


class FolderPayload(BaseModel):
    path: str
    name: str = "uploaded-docs"
    recursive: bool = True


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, _exc: RequestValidationError):
    return JSONResponse({"error": "invalid request"}, status_code=400)


@app.get("/")
async def home():
    return FileResponse(INDEX_HTML)


@app.get("/about")
async def about(index: DocsIndex = Depends(shared_index)):
    provider = index.embedder
    rows = await index.sources()
    total_pages = sum(r.get("pages", 0) for r in rows)
    total_chunks = sum(r.get("chunks", 0) for r in rows)
    return {
        "version": __version__,
        "python": sys.version.split()[0],
        "embedding_model": provider.name,
        "embedding_dims": provider.dimensions,
        "llm_model": settings.llm_model or "(not configured)",
        "sources": len(rows),
        "pages": total_pages,
        "chunks": total_chunks,
        "max_depth": settings.crawl_max_depth,
        "max_pages": settings.crawl_max_pages,
    }


@app.get("/search")
async def search(
    q: str = Query(default=""),
    mode: str = Query(default="hybrid"),
    name: str | None = Query(default=None),
    version: str | None = Query(default=None),
    k: int = Query(default=5),
    min_sim: float = Query(default=0.35),
    index: DocsIndex = Depends(shared_index),
):
    if not q:
        return JSONResponse({"error": "query param 'q' is required"}, status_code=400)
    try:
        hits = await index.search(q, name=name, version=version, k=k, mode=mode, min_similarity=min_sim)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"query": q, "mode": mode, "hits": [asdict(hit) for hit in hits]}


@app.get("/sources")
async def sources(index: DocsIndex = Depends(shared_index)):
    rows = await index.sources()
    for row in rows:
        if isinstance(row["updated_at"], datetime):
            row["updated_at"] = row["updated_at"].isoformat()
    return {"sources": rows}


@app.delete("/sources/{source_id}")
async def delete_source(source_id: str, index: DocsIndex = Depends(shared_index)):
    deleted = await index.delete(source_id)
    if not deleted:
        return JSONResponse({"error": f"unknown source: {source_id}"}, status_code=404)
    return {"deleted": deleted, "source_id": source_id}


@app.post("/ingest")
async def ingest(payload: IngestPayload, index: DocsIndex = Depends(shared_index)):
    outcome = await ingest_or_submit(index, **payload.model_dump())
    if isinstance(outcome, Job):
        return JSONResponse({"job_id": outcome.id, "status": outcome.status, "poll": f"/jobs/{outcome.id}"}, status_code=202)
    return asdict(outcome)


@app.post("/upload")
async def upload(name: str = Form(default="uploaded-docs"), files: list[UploadFile] | None = File(default=None), index: DocsIndex = Depends(shared_index)):
    if not files:
        return JSONResponse({"error": "no files provided"}, status_code=400)
    uploaded: list[tuple[str, bytes]] = []
    for upload_file in files:
        content = await upload_file.read()
        if content:
            uploaded.append((upload_file.filename or "uploaded-file", content))
    if not uploaded:
        return JSONResponse({"error": "no files provided"}, status_code=400)
    result = await index.ingest_files(name, uploaded)
    return asdict(result)


@app.post("/upload-folder")
async def upload_folder(payload: FolderPayload, index: DocsIndex = Depends(shared_index)):
    if not payload.path:
        return JSONResponse({"error": "missing required field: path"}, status_code=400)
    result = await index.ingest_folder(payload.name, payload.path, payload.recursive)
    return asdict(result)


@app.post("/ingest-deps")
async def ingest_deps(file: UploadFile | None = File(default=None), max_deps: int = Form(default=20)):
    if file is None:
        return JSONResponse({"error": "no file provided"}, status_code=400)
    filename = file.filename or "deps.txt"
    content = (await file.read()).decode("utf-8", errors="replace")
    try:
        return await resolve_dependencies(filename, content, max_deps=max_deps)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.get("/jobs")
async def list_jobs():
    return {"jobs": [job.to_dict() for job in JOBS.list()]}


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        return JSONResponse({"error": f"unknown job: {job_id}"}, status_code=404)
    return job.to_dict()


@app.get("/llm-chat")
async def llm_chat(q: str = Query(default=""), index: DocsIndex = Depends(shared_index)):
    if not q:
        return JSONResponse({"error": "Missing 'q' parameter"}, status_code=400)
    try:
        return await answer_question(index, q)
    except Exception as exc:
        logger.exception("Error in llm_chat endpoint")
        return JSONResponse({"error": f"Processing failed: {exc}"}, status_code=500)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
