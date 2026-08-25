# Codemap: `src/docs_mcp/processing/`

## Responsibility

This directory converts raw HTML documentation pages into heading-aware
markdown chunks suitable for embedding. It is the text-processing stage of the
ingestion pipeline: it takes HTML in and produces semantically-bounded text
fragments out, each tagged with its location in the document's heading
hierarchy.

Two stages, two modules:

- **`extract.py`** — HTML → markdown. Strips boilerplate (nav, scripts,
  footers) and returns clean markdown text.
- **`chunker.py`** — markdown → `Chunk` list. Splits markdown along heading
  boundaries, merges tiny sections, and packs oversized sections into
  overlapping fragments that fit within an embedding-friendly size budget.

`__init__.py` is empty; the package exposes its two modules directly.

## Design Patterns

**Dataclass-based chunks.** `Chunk` is a frozen-ish dataclass carrying the
chunk text and a `heading_path: list[str]` — the ordered list of ancestor
headings from the page root down to the section the chunk lives in. The
`breadcrumb` property renders this as a `" > "`-joined string for display or
metadata. An internal `_Section` dataclass holds the intermediate
representation between splitting and packing.

**Heading hierarchy tracking.** `split_sections` walks the markdown line by
line, maintaining a stack of `(level, title)` tuples. When a heading is
encountered, the stack is popped until the top is shallower than the current
heading level, then the new heading is pushed. The current path is always the
materialized stack. This produces a correct ancestor chain for every section
without a separate tree structure.

**Graceful degradation in extraction.** `html_to_markdown` tries
`trafilatura.extract` first (precision-favored, links and tables included,
images excluded). On failure or empty output it falls back to a
BeautifulSoup-based path that decomposes known junk selectors and runs
`markdownify` with ATX-style headings. Both paths pass through
`_collapse_blank`, which collapses runs of blank lines to at most one.

**Size-driven packing with overlap.** `pack_section` accumulates paragraphs
into a buffer up to `max_chars`; when the buffer would overflow it emits the
chunk and seeds the next buffer with a tail-overlap prefix drawn from the
emitted chunk's trailing sentences. Oversized paragraphs are word-split by
`_split_long_paragraph`. This keeps chunks within the embedding model's
context budget while preserving continuity across boundaries.

## Data & Control Flow

```
HTML string
  │
  ▼  html_to_markdown(html, url)            extract.py
  │   trafilatura.extract → _collapse_blank
  │   on failure/empty → _fallback_markdown (BeautifulSoup + markdownify)
  │
  ▼  markdown string
  │
  ▼  chunk_markdown(markdown)              chunker.py
  │   1. split_sections(markdown)
  │        line-by-line scan, HEADING_RE matches drive a level stack
  │        → list[_Section(heading_path, text)]
  │   2. merge_small_sections(sections)
  │        sections shorter than MIN_SECTION_CHARS are folded into the
  │        previous section when it is an ancestor and the combined size
  │        stays within 2× MAX_CHUNK_CHARS
  │   3. for each section: pack_section(section, max_chars, overlap)
  │        paragraph-split → split long paragraphs → buffer-accumulate
  │        with tail-overlap on overflow
  │        → list[str] pieces
  │   4. wrap each piece in Chunk(content, heading_path)
  │
  ▼  list[Chunk]
```

**Constants** (`chunker.py`):

| Constant             | Value | Meaning                                   |
|----------------------|-------|-------------------------------------------|
| `MAX_CHUNK_CHARS`    | 3500  | Soft cap on chunk text length             |
| `MIN_SECTION_CHARS`  | 200   | Sections shorter than this get merged     |
| `OVERLAP_CHARS`      | 250   | Tail-overlap budget between adjacent chunks|

`HEADING_RE` matches ATX headings `#{1,6} title`, capturing the level from
the hash count and tolerating trailing `#` and whitespace.

## Integration Points

**Dependencies (external):**

- `trafilatura` — primary HTML-to-markdown extractor.
- `bs4` (BeautifulSoup) — fallback HTML parsing and junk-node removal.
- `markdownify` — HTML-to-markdown conversion in the fallback path.

**Dependencies (internal):** none. This package has no intra-project imports;
it is a leaf module.

**Consumers:**

- `docs_mcp.pipeline.ingest_documentation` — the ingestion pipeline imports
  both `html_to_markdown` and `chunk_markdown`. For each crawled page it
  extracts markdown, computes a content hash for change detection, chunks the
  markdown, and emits one row per `Chunk` into the embedding batch. Each row
  carries `chunk.content`, `chunk.heading_path`, the page URL, title, and a
  stable `chunk_index` within the page. Rows are embedded in batches of 32
  and upserted into the database.

**Public API:**

| Symbol                       | Module       | Signature                                  |
|------------------------------|--------------|--------------------------------------------|
| `html_to_markdown`           | `extract`    | `(html: str, url: str) -> str \| None`    |
| `chunk_markdown`             | `chunker`    | `(markdown: str, max_chars?, overlap?) -> list[Chunk]` |
| `Chunk`                      | `chunker`    | dataclass: `content: str`, `heading_path: list[str]`, `breadcrumb: str` (property) |
