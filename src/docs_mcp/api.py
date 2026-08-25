import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from docs_mcp.config import settings
from docs_mcp.embeddings import get_embedding_provider
from docs_mcp.jobs import JOBS, submit_ingest
from docs_mcp.mcp_client import (
    McpBridge,
    maybe_json,
    parse_search_markdown,
    parse_sources_lines,
)
from docs_mcp.pipeline import ingest_documentation
from docs_mcp.storage.db import Database
from docs_mcp.llm import generate_llm_response
from docs_mcp.chat import handle_chat

logger = logging.getLogger(__name__)

INDEX_HTML = Path(__file__).parent / "static" / "index.html"

db = Database(settings.database_url)
bridge = McpBridge()


def _source_pattern(name: str | None, version: str | None) -> str | None:
    if name and version:
        return f"{name}@{version}"
    if name:
        return f"{name}@%"
    if version:
        return f"%@{version}"
    return None


@asynccontextmanager
async def lifespan(app):
    provider = get_embedding_provider()
    await db.ensure_schema(provider.dimensions)
    logger.info(
        "embedding model ready: %s (%d dims)", provider.name, provider.dimensions
    )
    yield
    await bridge.close()
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
    try:
        hits = await db.search(
            vectors[0],
            query_text=query,
            pattern=_source_pattern(
                request.query_params.get("name"), request.query_params.get("version")
            ),
            k=max(1, min(k, 20)),
            mode=mode,
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


async def index(request):
    return FileResponse(INDEX_HTML)


async def mcp_search(request):
    query = request.query_params.get("q")
    if not query:
        return JSONResponse({"error": "query param 'q' is required"}, status_code=400)
    try:
        text = await bridge.call(
            "search_documentation",
            {
                "query": query,
                "name": request.query_params.get("name") or None,
                "version": request.query_params.get("version") or None,
                "k": max(1, min(int(request.query_params.get("k", 5)), 20)),
                "mode": request.query_params.get("mode", "hybrid"),
            },
            timeout=120.0,
        )
    except (TimeoutError, RuntimeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    hits = parse_search_markdown(text)
    return JSONResponse({"query": query, "mode": request.query_params.get("mode", "hybrid"), "backend": "mcp", "hits": hits})


async def mcp_sources(request):
    try:
        text = await bridge.call("list_sources", {}, timeout=60.0)
    except (TimeoutError, RuntimeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    return JSONResponse({"sources": parse_sources_lines(text), "backend": "mcp"})


async def mcp_ingest(request):
    try:
        payload = await request.json()
        args = {
            "name": payload["name"],
            "version": payload["version"],
            "base_url": payload["base_url"],
            "max_depth": int(payload.get("max_depth") or 2),
            "max_pages": int(payload.get("max_pages") or 30),
            "background": bool(payload.get("background")),
            "prune_missing": bool(payload.get("prune_missing")),
        }
    except ValueError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    except KeyError as exc:
        return JSONResponse(
            {"error": f"missing required field: {exc}"}, status_code=400
        )
    try:
        text = await bridge.call("add_documentation", args, timeout=900.0)
    except (TimeoutError, RuntimeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    status_code = 202 if args["background"] else 200
    return JSONResponse(maybe_json(text), status_code=status_code)


async def mcp_job_status(request):
    try:
        text = await bridge.call(
            "get_ingest_status", {"job_id": request.path_params["job_id"]}, timeout=30.0
        )
    except (TimeoutError, RuntimeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    data = maybe_json(text)
    if "error" in data and "result" not in data and "status" not in data:
        return JSONResponse(data, status_code=404)
    return JSONResponse(data)


async def llm_chat(request):
    """LLM-powered chat using MCP-retrieved documentation."""
    query = request.query_params.get("q")
    if not query:
        return JSONResponse({"error": "Missing 'q' parameter"}, status_code=400)
    try:
        action = await handle_chat(db, query)

        if action["mode"] == "ingest_started":
            return JSONResponse({
                "answer": action["answer"],
                "sources": [],
                "job_id": action.get("job_id"),
            })

        if action["mode"] == "ingest_ask_url":
            return JSONResponse({
                "answer": action["answer"],
                "sources": [],
            })

        raw_response = await bridge.call(
            "search_documentation", {"query": query, "k": 5, "mode": "hybrid"}
        )
        hits = parse_search_markdown(raw_response)
        if not hits:
            return JSONResponse({"answer": "No matching documentation found.", "sources": []})
        context_lines = []
        for hit in hits:
            header = hit.get("title") or hit.get("url", "Unnamed")
            if hit.get("heading_path"):
                header += " — " + " > ".join(hit["heading_path"])
            context_lines.append(f"**{header}**\n\n{hit.get('content', '')}")
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
            url = hit.get("url", "")
            if url in seen_urls:
                continue
            seen_urls.add(url)
            sources.append(
                {
                    "title": hit.get("title") or url,
                    "url": url,
                    "heading_path": hit.get("heading_path", []),
                    "content": hit.get("content", ""),
                }
            )
        return JSONResponse({"answer": answer, "sources": sources})
    except Exception as exc:
        logger.exception("Error in llm_chat endpoint")
        return JSONResponse({"error": f"Processing failed: {exc}"}, status_code=500)



routes = [
    Route("/", index, methods=["GET"]),
    Route("/search", search, methods=["GET"]),
    Route("/sources", sources, methods=["GET"]),
    Route("/sources/{source_id}", delete_source, methods=["DELETE"]),
    Route("/ingest", ingest, methods=["POST"]),
    Route("/jobs", list_jobs, methods=["GET"]),
    Route("/jobs/{job_id}", get_job, methods=["GET"]),
    Route("/mcp/search", mcp_search, methods=["GET"]),
    Route("/mcp/sources", mcp_sources, methods=["GET"]),
    Route("/mcp/ingest", mcp_ingest, methods=["POST"]),
    Route("/mcp/jobs/{job_id}", mcp_job_status, methods=["GET"]),
    Route("/llm-chat", llm_chat, methods=["GET"]),
]

app = Starlette(routes=routes, lifespan=lifespan)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
