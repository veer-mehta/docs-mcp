import json
import logging
from datetime import datetime

from mcp.server.mcpserver import MCPServer

from docs_mcp.config import settings
from docs_mcp.jobs import JOBS, submit_ingest
from docs_mcp.pipeline import ingest_documentation, ingest_folder
from docs_mcp.storage.db import Database, source_pattern

logger = logging.getLogger(__name__)

mcp = MCPServer("fathom-mcp")
db = Database(settings.database_url)


@mcp.tool()
async def add_documentation(
    name: str,
    version: str,
    base_url: str,
    max_depth: int = 2,
    max_pages: int = 30,
    background: bool = False,
    prune_missing: bool = False,
    lang: str = "",
    sitemap: bool = False,
) -> str:
    """Crawl a framework/library documentation site and index it for semantic search.

    Re-ingesting an existing source is incremental: pages whose extracted
    markdown is unchanged are skipped (no re-embedding).

    Args:
        name: Short identifier for the framework, e.g. "react".
        version: Version string, e.g. "18.3" or "latest".
        base_url: Entry point URL of the documentation site.
        max_depth: How many link hops to follow from base_url.
        max_pages: Hard cap on the number of pages crawled.
        background: If true, start the crawl and return a job id immediately;
            track it with get_ingest_status. Large sites should use this to
            avoid tool timeouts.
        prune_missing: If true, delete indexed pages that this crawl did not
            visit. Only enable when depth/page caps cover the whole site,
            otherwise capped crawls would delete valid pages.
        lang: ISO 639-1 language code to filter pages (e.g. "en").
            Only pages matching this language are crawled.
        sitemap: If true, discover pages from sitemap.xml instead of
            following links. Gives better coverage for docs sites
            that expose a sitemap.
    """
    if background:
        job = submit_ingest(
            db,
            name=name,
            version=version,
            base_url=base_url,
            max_depth=max_depth,
            max_pages=max_pages,
            prune_missing=prune_missing,
            lang=lang,
            sitemap=sitemap,
        )
        return json.dumps(
            {
                "job_id": job.id,
                "source_id": job.source_id,
                "status": job.status,
                "note": f'Poll get_ingest_status(job_id="{job.id}") until status is done or failed.',
            }
        )
    result = await ingest_documentation(
        db, name, version, base_url, max_depth=max_depth, max_pages=max_pages,
        lang=lang, sitemap=sitemap,
    )
    return json.dumps(result)


@mcp.tool()
async def get_ingest_status(job_id: str) -> str:
    """Check progress of a background add_documentation job.

    Args:
        job_id: The id returned when the job was submitted with background=true.
    """
    job = JOBS.get(job_id)
    if job is None:
        return (
            f"Unknown job id: {job_id}. Jobs are kept in memory; "
            "an id from a previous session is no longer valid."
        )
    return json.dumps(job.to_dict())


@mcp.tool()
async def search_documentation(
    query: str,
    name: str | None = None,
    version: str | None = None,
    k: int = 5,
    mode: str = "hybrid",
) -> str:
    """Semantic search over indexed documentation.

    Returns the most relevant markdown chunks with their source URL and
    heading path. Use add_documentation first if nothing is indexed yet.

    Args:
        query: What to look for, e.g. "how to define a loader".
        name: Optional framework name filter, e.g. "react".
        version: Optional version filter, e.g. "18.3".
        k: Number of chunks to return (1-20).
        mode: "hybrid" (default) fuses vector + keyword ranking;
            "vector" or "keyword" force a single strategy.
    """
    from docs_mcp.embeddings import get_embedding_provider

    provider = get_embedding_provider()
    await db.ensure_schema(provider.dimensions)
    vectors = await provider.embed([query])
    hits = await db.search(
        vectors[0],
        query_text=query,
        pattern=source_pattern(name, version),
        k=max(1, min(k, 20)),
        mode=mode,
    )
    if not hits:
        return "No matching documentation found. Call add_documentation first."
    blocks = []
    for hit in hits:
        header = hit.title or hit.url
        crumb = " > ".join(hit.heading_path)
        if crumb:
            header += f" — {crumb}"
        score = (
            f"relevance {hit.similarity:.2f}"
            if hit.similarity is not None
            else f"match {hit.bm25_score:.4f}"
        )
        blocks.append(f"### [{header}]({hit.url}) ({score})\n\n{hit.content}")
    return "\n\n---\n\n".join(blocks)


@mcp.tool()
async def list_sources() -> str:
    """List all indexed documentation sources with page and chunk counts."""
    rows = await db.list_sources()
    if not rows:
        return "No sources indexed yet."
    lines = []
    for row in rows:
        updated = row["updated_at"]
        if isinstance(updated, datetime):
            updated = updated.isoformat()
        lines.append(
            f"- {row['source_id']}: {row['pages']} pages, {row['chunks']} chunks "
            f"(updated {updated})"
        )
    return "\n".join(lines)


@mcp.tool()
async def add_local_docs(
    name: str,
    path: str,
    recursive: bool = True,
) -> str:
    """Index local documentation files from a folder on disk.

    Walks the folder, finds supported files (HTML, Markdown, PDF, TXT),
    extracts text, chunks, embeds, and stores them for semantic search.

    Args:
        name: Short identifier for this collection, e.g. "internal-api".
        path: Absolute or relative path to the folder on the server.
        recursive: If true, descend into subdirectories (default true).
    """
    from pathlib import Path

    resolved = Path(path).expanduser().resolve()
    if not resolved.is_dir():
        return f"Not a directory: {path}"
    result = await ingest_folder(db, name=name, folder_path=str(resolved), recursive=recursive)
    return json.dumps({
        "source_id": result.source_id,
        "files_indexed": result.pages_indexed,
        "chunks_indexed": result.chunks_indexed,
        "errors": result.errors,
        "note": f"Search with search_documentation(name=\"{name}\").",
    })


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    mcp.run(transport=settings.mcp_transport)


if __name__ == "__main__":
    main()
