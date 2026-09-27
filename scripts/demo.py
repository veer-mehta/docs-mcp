"""Use docs_mcp as a plain Python library — no MCP, no HTTP.

.venv/bin/python scripts/demo.py
"""

import asyncio

from docs_mcp.index import DocsIndex


async def main() -> None:
    index = await DocsIndex.open()

    sources = await index.sources()
    if not any(s["source_id"] == "pydantic@2.13" for s in sources):
        print("ingesting pydantic@2.13 ...")
        result = await index.ingest_site("pydantic", "2.13", "https://docs.pydantic.dev/latest/", max_depth=1, max_pages=4)
        print("ingest result:", result)

    for query in ["how do I install pydantic", "migrating from v1 to v2"]:
        hits = await index.search(query, name="pydantic", k=2, mode="vector")
        print(f"\n{query}")
        for hit in hits:
            crumb = " > ".join(hit.heading_path)
            print(f"  {hit.similarity:.2f}  {crumb or '(root)'}  ->  {hit.url}")

    await index.close()


if __name__ == "__main__":
    asyncio.run(main())
