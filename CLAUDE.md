# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

## Commands (additions)

- Setup: `pip install -e ".[local,dev]"` into `.venv`, `docker compose up -d postgres`, `cp .env.example .env`.
- Single test: `.venv/bin/pytest tests/test_chunker.py::test_breadcrumbs_follow_heading_hierarchy -q`

## Architecture

**`DocsIndex` (`index.py`) is the core.** It owns the store (`store.ChunkStore`), the
embedder, and the schema: `search`, `ingest_site`, `ingest_files`, `ingest_folder`,
`sources`, `delete`. `DocsIndex.open()` builds the embedder once and runs
`ensure_schema` once; `shared_index()` is the process-wide instance. Everything else
receives a `DocsIndex` instead of wiring `ChunkStore` + provider itself.

**Adapters** (`adapters/`) only translate requests in and results out:
`mcp_server.py` (MCP over stdio; opens the index lazily on first tool call so
`initialize` isn't blocked by model load) and `http_api.py` (FastAPI; index injected
via `Depends(shared_index)`, serves `static/index.html`). `docs_mcp/server.py` is a
shim kept for existing `python -m docs_mcp.server` client configs.

**Ingestion (`pipeline.py`)**: the Scrapy spider always runs as a subprocess
(`python -m docs_mcp.scraper.runner`) because the Twisted reactor cannot restart inside
a long-lived async process. It emits one JSON page per stdout line →
`processing/extract` → `processing/chunker` (heading-aware, keeps `heading_path`) →
embed in batches of 8 → `ChunkStore.upsert_chunks`. Pages whose markdown SHA-256
matches the stored `content_hash` are skipped. `prune_missing` deletes stale URLs only
when the crawler exited 0. All ingest paths return `IngestResult`.

**Sources** are `name@version` (`source_id`); uploads and local folders become
`name@latest` with `file:///` URLs. Name/version filters become SQL `LIKE` patterns via
`store.source_pattern`.

**Storage (`store.py`)**: one table (`documents`) with HNSW (cosine) + GIN full-text
indexes. The `vector(dim)` column is fixed at creation — switching embedding
provider/model dimensions raises and requires dropping the table and re-ingesting.
Search modes: `vector`, `keyword` (skips embedding), `hybrid` (default; Reciprocal Rank
Fusion, k=60).

**Embeddings (`embeddings/`)**: `get_embedding_provider()` (cached) picks `local.py`
(sentence-transformers, optional `[local]` extra) or `remote.py` (any OpenAI-compatible
endpoint) from `EMBEDDING_PROVIDER`. Providers expose `name`, `dimensions`,
`async embed(texts)`.

**Jobs (`jobs.py`)**: in-memory `JobRegistry` of asyncio tasks (lost on restart, capped
at 100). `ingest_or_submit` returns `Job | IngestResult`; both adapters use it, and the
HTTP adapter maps it to 202/200.

**Other**: `answer.py` does retrieval + an OpenAI-compatible chat completion
(`LLM_*` settings) for `/llm-chat`. `deps/manifests.py` parses
requirements/pyproject/package(-lock) files and `deps/registry.py` resolves doc URLs
via PyPI/npm for `POST /ingest-deps`.

**Config (`config.py`)**: pydantic-settings reads `~/.fathom-mcp/.env` then the repo
`.env` (later wins). `.env.example` lists every key.

## Testing

Test at the `DocsIndex` interface: `DocsIndex(tests.fakes.InMemoryStore(),
tests.fakes.HashEmbeddingProvider())`. API tests override the dependency with
`app.dependency_overrides[shared_index]`.

## npm distribution

`npm/` is the `@fathom-mcp/server` package: `bin/docs-mcp-server.js` copies the bundled
`npm/python/` into `~/.fathom-mcp/src`, creates a venv there, and starts Postgres via
docker compose. `npm/python/src/docs_mcp` is a hand-maintained copy of `src/docs_mcp` with
no sync script and currently lags behind — edits to `src/` do not reach the npm package
unless copied over deliberately.
