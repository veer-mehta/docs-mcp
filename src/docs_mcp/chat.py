import asyncio
import re
import logging
from dataclasses import dataclass

from docs_mcp.config import settings
from docs_mcp.jobs import submit_ingest, JOBS
from docs_mcp.storage.db import Database

logger = logging.getLogger(__name__)

KNOWN_DOCS: dict[str, dict[str, str]] = {
    "pydantic": {"url": "https://docs.pydantic.dev/latest/", "name": "pydantic"},
    "fastapi": {"url": "https://fastapi.tiangolo.com/", "name": "fastapi"},
    "langchain": {"url": "https://python.langchain.com/docs/", "name": "langchain"},
    "django": {"url": "https://docs.djangoproject.com/en/5.1/", "name": "django"},
    "flask": {"url": "https://flask.palletsprojects.com/en/3.1.x/", "name": "flask"},
    "sqlalchemy": {"url": "https://docs.sqlalchemy.org/en/20/", "name": "sqlalchemy"},
    "celery": {"url": "https://docs.celeryq.dev/en/stable/", "name": "celery"},
    "react": {"url": "https://react.dev/reference/react", "name": "react"},
    "nextjs": {"url": "https://nextjs.org/docs", "name": "nextjs"},
    "tailwind": {"url": "https://tailwindcss.com/docs", "name": "tailwindcss"},
    "vue": {"url": "https://vuejs.org/guide/introduction.html", "name": "vue"},
    "svelte": {"url": "https://svelte.dev/docs/svelte/overview", "name": "svelte"},
    "rust": {"url": "https://doc.rust-lang.org/book/", "name": "rust"},
    "go": {"url": "https://go.dev/doc/", "name": "go"},
    "postgres": {"url": "https://www.postgresql.org/docs/current/", "name": "postgresql"},
    "redis": {"url": "https://redis.io/docs/latest/", "name": "redis"},
    "docker": {"url": "https://docs.docker.com/get-started/", "name": "docker"},
    "kubernetes": {"url": "https://kubernetes.io/docs/home/", "name": "kubernetes"},
    "terraform": {"url": "https://developer.hashicorp.com/terraform/docs", "name": "terraform"},
    "openai": {"url": "https://platform.openai.com/docs", "name": "openai"},
    "anthropic": {"url": "https://docs.anthropic.com/en/docs", "name": "anthropic"},
    "gemini": {"url": "https://ai.google.dev/gemini-api/docs", "name": "gemini"},
}

INGEST_PATTERN = re.compile(
    r"(?:download|get|fetch|ingest|add|install)\s+"
    r"(?:the\s+)?(?P<name>[\w.-]+?)(?:\s+(?:v?(?P<version>[\d.]+))?)?\s+"
    r"(?:docs?|documentation|reference|guide)",
    re.IGNORECASE,
)


@dataclass
class ChatAction:
    kind: str  # "ingest" | "chat"
    name: str | None = None
    version: str | None = None
    url: str | None = None
    message: str = ""


def detect_intent(query: str) -> ChatAction:
    m = INGEST_PATTERN.search(query)
    if not m:
        return ChatAction(kind="chat", message=query)

    name = m.group("name").lower().rstrip("s")
    version = m.group("version") or "latest"
    known = KNOWN_DOCS.get(name)

    if known:
        return ChatAction(
            kind="ingest",
            name=known["name"],
            version=version,
            url=known["url"],
        )
    return ChatAction(
        kind="ingest",
        name=name,
        version=version,
        url=None,
        message=query,
    )


async def handle_chat(db: Database, query: str) -> dict:
    action = detect_intent(query)

    if action.kind == "chat":
        return {"mode": "chat", "query": query}

    if not action.url:
        return {
            "mode": "ingest_ask_url",
            "name": action.name,
            "version": action.version,
            "answer": (
                f"I don't have a known URL for **{action.name}**. "
                f"Please provide the documentation URL and I'll download it.\n\n"
                f"Example: `download {action.name} docs from https://example.com/docs`"
            ),
            "sources": [],
        }

    job = submit_ingest(
        db,
        name=action.name,
        version=action.version,
        base_url=action.url,
        max_depth=2,
        max_pages=50,
        prune_missing=True,
    )

    return {
        "mode": "ingest_started",
        "job_id": job.id,
        "name": action.name,
        "version": action.version,
        "answer": (
            f"Starting download of **{action.name} v{action.version}** docs...\n\n"
            f"Source: {action.url}\n"
            f"Job: `{job.id}`\n\n"
            f"Check progress in the **Sources** tab, or ask me anything once it's done."
        ),
        "sources": [],
    }
