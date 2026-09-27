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

from docs_mcp import __version__
from docs_mcp.config import settings
from docs_mcp.doc_finder import resolve_dependencies
from docs_mcp.index import DocsIndex, shared_index
from docs_mcp.jobs import JOBS, ingest_or_submit
from docs_mcp.llm import answer_question

logger = logging.getLogger(__name__)

INDEX_HTML = Path(__file__).parent / "static" / "index.html"


@asynccontextmanager
async def lifespan(app):
    index = await shared_index()
    logger.info("embedding model ready: %s (%d dims)", index.embedder.name, index.embedder.dimensions)
    yield
    await index.close()


app = FastAPI(title="fathom-mcp", version=__version__, lifespan=lifespan)


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
        "database": settings.database_url.split("@")[-1] if "@" in settings.database_url else settings.database_url,
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
    body, status = await ingest_or_submit(index, **payload.model_dump())
    return JSONResponse(body, status_code=status)


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
