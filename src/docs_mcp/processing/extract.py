import logging
import re
from pathlib import Path

import trafilatura
from bs4 import BeautifulSoup
from markdownify import markdownify as md

logger = logging.getLogger(__name__)

_JUNK_SELECTORS = [
    "script",
    "style",
    "noscript",
    "nav",
    "footer",
    "[aria-hidden='true']",
]


def _collapse_blank(text: str) -> str:
    text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _fallback_markdown(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for selector in _JUNK_SELECTORS:
        for node in soup.select(selector):
            node.decompose()
    root = soup.body or soup
    return _collapse_blank(md(str(root), heading_style="ATX"))


def html_to_markdown(html: str, url: str) -> str | None:
    try:
        result = trafilatura.extract(
            html,
            url=url,
            output_format="markdown",
            include_links=True,
            include_tables=True,
            include_images=False,
            with_metadata=False,
            favor_precision=True,
        )
    except Exception:
        logger.exception("trafilatura extraction failed for %s", url)
        result = None
    if result and result.strip():
        return _collapse_blank(result)
    fallback = _fallback_markdown(html)
    return fallback or None


def file_to_markdown(path: Path, filename: str) -> str | None:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ("md", "txt"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            return _collapse_blank(text) or None
        except Exception:
            logger.exception("failed to read %s", filename)
            return None
    if ext in ("html", "htm"):
        try:
            return html_to_markdown(path.read_text(encoding="utf-8", errors="replace"), filename)
        except Exception:
            logger.exception("failed to read %s", filename)
            return None
    if ext == "pdf":
        try:
            import pymupdf
            doc = pymupdf.open(str(path))
            pages = []
            for page in doc:
                pages.append(page.get_text())
            doc.close()
            text = "\n\n".join(pages)
            return _collapse_blank(text) or None
        except Exception:
            logger.exception("failed to extract PDF %s", filename)
            return None
    try:
        raw = path.read_bytes()
        if b"\x00" in raw[:8192]:
            return None
        text = raw.decode("utf-8", errors="replace")
        return _collapse_blank(text) or None
    except Exception:
        return None
