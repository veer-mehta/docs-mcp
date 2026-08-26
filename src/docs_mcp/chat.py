import re

from docs_mcp.jobs import submit_ingest
from docs_mcp.storage.db import Database

INGEST_PATTERN = re.compile(
    r"(?:download|get|fetch|ingest|add|install)\s+"
    r"(?:the\s+)?(?P<name>[\w.-]+?)(?:\s+(?:v?(?P<version>[\d.]+))?)?\s+"
    r"(?:docs?|documentation|reference|guide)",
    re.IGNORECASE,
)


def handle_chat(query: str) -> dict:
    m = INGEST_PATTERN.search(query)
    if not m:
        return {"mode": "chat", "query": query}
    name = m.group("name").lower().rstrip("s")
    version = m.group("version") or "latest"
    return {
        "mode": "ingest_ask_url",
        "name": name,
        "version": version,
        "answer": (
            f"I can download **{name}** docs for you. "
            f"Please provide the documentation URL.\n\n"
            f"Example: `download {name} docs from https://example.com/docs`"
        ),
    }


def start_ingest(db: Database, name: str, version: str, url: str) -> dict:
    job = submit_ingest(
        db,
        name=name,
        version=version,
        base_url=url,
        max_depth=2,
        max_pages=50,
        prune_missing=True,
    )
    return {
        "job_id": job.id,
        "answer": (
            f"Starting download of **{name} v{version}** docs...\n\n"
            f"Source: {url}\n"
            f"Job: `{job.id}`\n\n"
            f"Check progress in the **Sources** tab, or ask me anything once it's done."
        ),
    }
