"""Use docs_mcp as a plain Python library — no MCP, no HTTP.

.venv/bin/python scripts/demo.py
"""

import asyncio

from docs_mcp.config import settings
from docs_mcp.embeddings import get_embedding_provider
from docs_mcp.pipeline import ingest_documentation
from docs_mcp.storage.db import Database


async def main() -> None:
    db = Database(settings.database_url)
    provider = get_embedding_provider()
    await db.ensure_schema(provider.dimensions)

    sources = await db.list_sources()
    if not any(s["source_id"] == "pydantic@2.13" for s in sources):
        print("ingesting pydantic@2.13 ...")
        result = await ingest_documentation(db, name="pydantic", version="2.13", base_url="https://docs.pydantic.dev/latest/", max_depth=1, max_pages=4)
        print("ingest result:", result)

    for query in ["how do I install pydantic", "migrating from v1 to v2"]:
        vectors = await provider.embed([query])
        hits = await db.search(vectors[0], pattern="pydantic@%", k=2)
        print(f"\n{query}")
        for hit in hits:
            crumb = " > ".join(hit.heading_path)
            print(f"  {hit.similarity:.2f}  {crumb or '(root)'}  ->  {hit.url}")

    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
