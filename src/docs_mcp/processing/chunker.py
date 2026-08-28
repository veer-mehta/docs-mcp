import re
import textwrap
from dataclasses import dataclass

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")

MAX_CHUNK_CHARS = 3500
MIN_SECTION_CHARS = 200
OVERLAP_CHARS = 250


@dataclass
class Chunk:
    content: str
    heading_path: list[str]


def split_sections(markdown: str) -> list[tuple[list[str], str]]:
    sections: list[tuple[list[str], str]] = []
    stack: list[tuple[int, str]] = []
    path: list[str] = []
    body: list[str] = []

    def flush() -> None:
        text = "\n".join(body).strip()
        if text:
            sections.append((list(path), text))

    for line in markdown.splitlines():
        match = HEADING_RE.match(line)
        if match:
            flush()
            body = []
            level = len(match.group(1))
            title = match.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            path = [title for _, title in stack]
        else:
            body.append(line)
    flush()
    return sections


def merge_small_sections(sections: list[tuple[list[str], str]]) -> list[tuple[list[str], str]]:
    merged: list[tuple[list[str], str]] = []
    for heading_path, text in sections:
        if merged and len(text) < MIN_SECTION_CHARS:
            prev_path, prev_text = merged[-1]
            is_ancestor = (
                len(prev_path) <= len(heading_path)
                and heading_path[: len(prev_path)] == prev_path
            )
            fits = len(prev_text) + len(text) + 2 <= MAX_CHUNK_CHARS * 2
            if is_ancestor and fits:
                merged[-1] = (prev_path, f"{prev_text}\n\n{text}")
                continue
        merged.append((heading_path, text))
    return merged


def _paragraphs(text: str) -> list[str]:
    parts = re.split(r"\n\s*\n", text)
    return [part.strip() for part in parts if part.strip()]


def _tail_overlap(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    tail = ""
    for sentence in reversed(sentences):
        candidate = f"{sentence} {tail}".strip() if tail else sentence
        if len(candidate) > limit:
            break
        tail = candidate
    if tail:
        return tail
    return text[-limit:]


def pack_section(section: tuple[list[str], str], max_chars: int, overlap: int) -> list[str]:
    heading_path, text = section
    paragraphs: list[str] = []
    for paragraph in _paragraphs(text):
        if len(paragraph) > max_chars:
            paragraphs.extend(textwrap.wrap(paragraph, width=max_chars))
        else:
            paragraphs.append(paragraph)

    chunks: list[str] = []
    buffer = ""
    for paragraph in paragraphs:
        if not buffer:
            buffer = paragraph
        elif len(buffer) + len(paragraph) + 2 > max_chars:
            chunks.append(buffer)
            tail = _tail_overlap(buffer, overlap)
            buffer = f"{tail}\n\n{paragraph}" if tail else paragraph
        else:
            buffer = f"{buffer}\n\n{paragraph}"
    if buffer:
        chunks.append(buffer)
    return chunks


def chunk_markdown(
    markdown: str,
    max_chars: int = MAX_CHUNK_CHARS,
    overlap: int = OVERLAP_CHARS,
) -> list[Chunk]:
    sections = merge_small_sections(split_sections(markdown))
    chunks: list[Chunk] = []
    for heading_path, text in sections:
        for piece in pack_section((heading_path, text), max_chars, overlap):
            piece = piece.strip()
            if piece:
                chunks.append(Chunk(content=piece, heading_path=list(heading_path)))
    return chunks
