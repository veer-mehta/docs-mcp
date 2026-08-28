import logging
import sys
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from docs_mcp import __version__
from docs_mcp.config import settings
from docs_mcp.doc_finder import detect_language, find_doc_url, filter_deps
from docs_mcp.embeddings import get_embedding_provider
from docs_mcp.jobs import JOBS, submit_ingest
from docs_mcp.parsers import parse_dep_file
from docs_mcp.pipeline import ingest_documentation, ingest_files, ingest_folder
from docs_mcp.storage.db import Database, source_pattern
from docs_mcp.llm import generate_llm_response

logger = logging.getLogger(__name__)

INDEX_HTML = Path(__file__).parent / "static" / "index.html"

db = Database(settings.database_url)


@asynccontextmanager
async def lifespan(app):
    provider = get_embedding_provider()
    await db.ensure_schema(provider.dimensions)
    logger.info(
        "embedding model ready: %s (%d dims)", provider.name, provider.dimensions
    )
    yield
    await db.close()


async def ingest(request):
    try:
        payload = await request.json()
        name, version, base_url = payload["name"], payload["version"], payload["base_url"]
    except ValueError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    except KeyError as exc:
        return JSONResponse(
            {"error": f"missing required field: {exc}"}, status_code=400
        )
    if payload.get("background"):
        job = submit_ingest(
            db,
            name=name,
            version=version,
            base_url=base_url,
            max_depth=payload.get("max_depth"),
            max_pages=payload.get("max_pages"),
            prune_missing=bool(payload.get("prune_missing")),
            lang=payload.get("lang", ""),
            sitemap=bool(payload.get("sitemap")),
        )
        return JSONResponse(
            {"job_id": job.id, "status": job.status, "poll": f"/jobs/{job.id}"},
            status_code=202,
        )
    result = await ingest_documentation(
        db,
        name=name,
        version=version,
        base_url=base_url,
        max_depth=payload.get("max_depth"),
        max_pages=payload.get("max_pages"),
        prune_missing=bool(payload.get("prune_missing")),
        lang=payload.get("lang", ""),
        sitemap=bool(payload.get("sitemap")),
    )
    return JSONResponse(result)


async def get_job(request):
    job = JOBS.get(request.path_params["job_id"])
    if job is None:
        return JSONResponse(
            {"error": f"unknown job: {request.path_params['job_id']}"}, status_code=404
        )
    return JSONResponse(job.to_dict())


async def list_jobs(request):
    return JSONResponse({"jobs": [job.to_dict() for job in JOBS.list()]})


async def search(request):
    query = request.query_params.get("q")
    if not query:
        return JSONResponse({"error": "query param 'q' is required"}, status_code=400)
    mode = request.query_params.get("mode", "hybrid")
    if mode not in ("hybrid", "vector", "keyword"):
        return JSONResponse(
            {"error": f"unknown mode: {mode} (use hybrid|vector|keyword)"},
            status_code=400,
        )
    provider = get_embedding_provider()
    vectors = await provider.embed([query])
    k = int(request.query_params.get("k", 5))
    min_sim = float(request.query_params.get("min_sim", 0.35))
    try:
        hits = await db.search(
            vectors[0],
            query_text=query,
            pattern=source_pattern(
                request.query_params.get("name"), request.query_params.get("version")
            ),
            k=max(1, min(k, 20)),
            mode=mode,
            min_similarity=min_sim,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"query": query, "mode": mode, "hits": [asdict(hit) for hit in hits]})


async def sources(request):
    rows = await db.list_sources()
    for row in rows:
        if isinstance(row["updated_at"], datetime):
            row["updated_at"] = row["updated_at"].isoformat()
    return JSONResponse({"sources": rows})


async def delete_source(request):
    source_id = request.path_params["source_id"]
    deleted = await db.delete_source(source_id)
    if not deleted:
        return JSONResponse(
            {"error": f"unknown source: {source_id}"}, status_code=404
        )
    return JSONResponse({"deleted": deleted, "source_id": source_id})


async def about(request):
    provider = get_embedding_provider()
    rows = await db.list_sources()
    total_pages = sum(r.get("pages", 0) for r in rows)
    total_chunks = sum(r.get("chunks", 0) for r in rows)
    return JSONResponse({
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
    })


async def index(request):
    return FileResponse(INDEX_HTML)


async def llm_chat(request):
    query = request.query_params.get("q")
    if not query:
        return JSONResponse({"error": "Missing 'q' parameter"}, status_code=400)
    try:
        provider = get_embedding_provider()
        vectors = await provider.embed([query])
        hits = await db.search(vectors[0], query_text=query, k=5, mode="hybrid")
        if not hits:
            return JSONResponse({"answer": "No matching documentation found.", "sources": []})
        context_lines = []
        for hit in hits:
            header = hit.title or hit.url
            if hit.heading_path:
                header += " — " + " > ".join(hit.heading_path)
            context_lines.append(f"**{header}**\n\n{hit.content}")
        context = "\n\n".join(context_lines)
        prompt = (
            "Answer the following question using only the provided context. "
            "Keep it concise and conversational.\n\n"
            f"Question: {query}\n\nContext:\n{context}"
        )
        answer = await generate_llm_response(prompt)
        sources: list[dict] = []
        seen_urls: set[str] = set()
        for hit in hits:
            if hit.url in seen_urls:
                continue
            seen_urls.add(hit.url)
            sources.append(
                {
                    "title": hit.title or hit.url,
                    "url": hit.url,
                    "heading_path": hit.heading_path,
                    "content": hit.content,
                }
            )
        return JSONResponse({"answer": answer, "sources": sources})
    except Exception as exc:
        logger.exception("Error in llm_chat endpoint")
        return JSONResponse({"error": f"Processing failed: {exc}"}, status_code=500)


async def upload(request):
    try:
        form = await request.form()
    except Exception:
        return JSONResponse({"error": "invalid form data"}, status_code=400)
    name = form.get("name", "uploaded-docs")
    files: list[tuple[str, bytes]] = []
    for key in form:
        if key == "name":
            continue
        upload_file = form[key]
        if hasattr(upload_file, "read"):
            content = await upload_file.read()
            filename = getattr(upload_file, "filename", key) or key
            if content:
                files.append((filename, content))
    if not files:
        return JSONResponse({"error": "no files provided"}, status_code=400)
    result = await ingest_files(db, name=name, files=files)
    return JSONResponse(asdict(result))


async def upload_folder(request):
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    path = payload.get("path")
    if not path:
        return JSONResponse({"error": "missing required field: path"}, status_code=400)
    name = payload.get("name", "uploaded-docs")
    recursive = bool(payload.get("recursive", True))
    result = await ingest_folder(db, name=name, folder_path=path, recursive=recursive)
    return JSONResponse(asdict(result))


async def ingest_deps(request):
    try:
        form = await request.form()
    except Exception:
        return JSONResponse({"error": "invalid form data"}, status_code=400)
    upload_file = None
    for key in form:
        if key == "name":
            continue
        f = form[key]
        if hasattr(f, "read"):
            upload_file = f
            break
    if not upload_file:
        return JSONResponse({"error": "no file provided"}, status_code=400)
    filename = upload_file.filename or "deps.txt"
    content = (await upload_file.read()).decode("utf-8", errors="replace")
    deps = parse_dep_file(filename, content)
    if not deps:
        return JSONResponse({"error": f"could not parse dependencies from {filename}"}, status_code=400)
    max_deps = int(form.get("max_deps", 20))
    deps = filter_deps(deps, max_deps=max_deps)
    lang = detect_language(deps)
    results = []
    for dep in deps:
        doc_url = await find_doc_url(dep)
        if doc_url:
            results.append({"name": dep.name, "version": dep.version, "url": doc_url, "ecosystem": dep.ecosystem})
    if lang and lang in ("python", "node"):
        from docs_mcp.doc_finder import LANGUAGE_DOCS
        lang_url = LANGUAGE_DOCS.get(lang)
        if lang_url:
            results.append({"name": lang, "version": "latest", "url": lang_url, "ecosystem": "language"})
    if not results:
        return JSONResponse({"error": "no documentation URLs found"}, status_code=404)
    return JSONResponse({
        "dependencies": results,
        "language": lang,
        "total": len(results),
    })


routes = [
    Route("/", index, methods=["GET"]),
    Route("/about", about, methods=["GET"]),
    Route("/search", search, methods=["GET"]),
    Route("/sources", sources, methods=["GET"]),
    Route("/sources/{source_id}", delete_source, methods=["DELETE"]),
    Route("/ingest", ingest, methods=["POST"]),
    Route("/upload", upload, methods=["POST"]),
    Route("/upload-folder", upload_folder, methods=["POST"]),
    Route("/ingest-deps", ingest_deps, methods=["POST"]),
    Route("/jobs", list_jobs, methods=["GET"]),
    Route("/jobs/{job_id}", get_job, methods=["GET"]),
    Route("/llm-chat", llm_chat, methods=["GET"]),
]

app = Starlette(routes=routes, lifespan=lifespan)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
