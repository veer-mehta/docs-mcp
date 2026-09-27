# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

## Commands (additions)

- Setup: `pip install -e ".[local,dev]"` into `.venv`, `docker compose up -d postgres`, `cp .env.example .env`.
- Single test: `.venv/bin/pytest tests/test_text.py::test_breadcrumbs_follow_heading_hierarchy -q`

## Architecture

Flat package, one module per concern (`src/docs_mcp/`):

| Module | Owns |
|---|---|
| `__init__.py` | `Settings` / `settings` (pydantic-settings), `__version__` |
| `index.py` | `DocsIndex`, the ingestion pipeline, background jobs |
| `store.py` | `ChunkStore`: Postgres + pgvector schema, upsert, search |
| `embeddings.py` | local (sentence-transformers) and remote (OpenAI-compatible) providers |
| `text.py` | HTML/PDF/file → markdown, heading-aware chunker |
| `crawler.py` | Scrapy spider + `docs-mcp-crawl` CLI (subprocess only) |
| `deps.py` | dependency-manifest parsing + PyPI/npm doc-URL lookup |
| `api.py` | FastAPI app, `/llm-chat` RAG answering, serves `index.html` |
| `server.py` | MCP tools over stdio |

**`DocsIndex` is the core.** It owns the store, embedder, and schema: `search`,
`ingest_site`, `ingest_files`, `ingest_folder`, `sources`, `delete`. `DocsIndex.open()`
builds the embedder once and runs `ensure_schema` once; `shared_index()` is the
process-wide instance. `api.py` injects it with `Depends(shared_index)`; `server.py`
opens it lazily on first tool call so MCP `initialize` isn't blocked by model load.

**Ingestion**: the crawler always runs as a subprocess (`python -m docs_mcp.crawler`)
because the Twisted reactor cannot restart inside a long-lived async process — never
import `docs_mcp.crawler` from the main process. It emits one JSON page per stdout line
→ `text.html_to_markdown` → `text.chunk_markdown` (keeps `heading_path`) → embed in
batches of 8 → `ChunkStore.upsert_chunks`. Pages whose markdown SHA-256 matches the
stored `content_hash` are skipped. `prune_missing` deletes stale URLs only when the
crawler exited 0. All ingest paths return `IngestResult`.

**Sources** are `name@version` (`source_id`); uploads and local folders become
`name@latest` with `file:///` URLs. Name/version filters become SQL `LIKE` patterns via
`store.source_pattern`.

**Storage**: one table (`documents`) with HNSW (cosine) + GIN full-text indexes. The
`vector(dim)` column is fixed at creation — switching embedding provider/model
dimensions raises and requires dropping the table and re-ingesting. Search modes:
`vector`, `keyword` (skips embedding; needs ≥2 terms longer than 2 chars), `hybrid`
(default; Reciprocal Rank Fusion, k=60).

**Jobs**: in-memory `JobRegistry` of asyncio tasks (lost on restart, capped at 100).
`ingest_or_submit` returns `Job | IngestResult`; both adapters use it, and `api.py` maps
it to 202/200.

**Config**: `settings` reads `~/.fathom-mcp/.env` then the repo `.env` (later wins).
`.env.example` lists every key. A git worktree has no `.env`, so it silently falls back to
defaults (384-dim model) — symlink the main checkout's `.env` before running the app there.

## Testing

Test at the `DocsIndex` interface: `DocsIndex(tests.fakes.InMemoryStore(),
tests.fakes.HashEmbeddingProvider())`. API tests override the dependency with
`app.dependency_overrides[shared_index]`. One test file per module: `test_text`,
`test_index`, `test_store`, `test_adapters` (api + server).

## npm distribution

`npm/` is the `@fathom-mcp/server` package: `bin/docs-mcp-server.js` copies the bundled
`npm/python/` into `~/.fathom-mcp/src`, creates a venv there, and starts Postgres via
docker compose. `npm/python/src/docs_mcp` is a hand-maintained copy of `src/docs_mcp` with
no sync script and currently lags behind (still the old multi-directory layout) — edits
to `src/` do not reach the npm package unless copied over deliberately.
