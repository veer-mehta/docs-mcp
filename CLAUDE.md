# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

## Commands (additions)

- Setup: `pip install -e ".[local,dev]"` into `.venv`, `docker compose up -d postgres`, `cp .env.example .env`.
- Single test: `.venv/bin/pytest tests/test_chunker.py::test_breadcrumbs_follow_heading_hierarchy -q`
- `tests/test_storage.py` is marked `integration` and needs the compose Postgres on
  `localhost:5432` (table `documents_test`). Without it: `.venv/bin/pytest -m "not integration"`.

## Architecture

Three entry points (`pyproject.toml` scripts) share one core in `src/docs_mcp/`:
`server.py` (FastMCP, stdio), `api.py` (FastAPI + `static/index.html` single-file UI),
`scraper/runner.py` (crawler CLI). `api.py` and `server.py` are thin adapters; the
orchestration lives in `pipeline.py`, `jobs.py`, `llm.py`, `doc_finder.py`.

**Ingestion (`pipeline.ingest_documentation`)**: the Scrapy spider always runs as a
subprocess (`python -m docs_mcp.scraper.runner`) because the Twisted reactor cannot
restart inside a long-lived async process. It emits one JSON page per stdout line →
`processing/extract.html_to_markdown` (trafilatura/markdownify) →
`processing/chunker.chunk_markdown` (heading-aware, keeps `heading_path`) → embed in
batches of 8 → `Database.upsert_chunks`. Pages are skipped when their markdown SHA-256
matches the stored `content_hash`. `prune_missing` deletes stale URLs only when the
crawler exited 0.

**Sources** are identified as `name@version` (`source_id`). Uploads and local folders
become `name@latest` with `file:///` URLs. Name/version filters become SQL `LIKE`
patterns via `storage/db.source_pattern`.

**Storage (`storage/db.py`)**: one table (`documents`), schema created lazily by
`ensure_schema(dim)` on every ingest/search, with HNSW (cosine) + GIN full-text indexes.
The `vector(dim)` column is fixed at creation — switching embedding provider/model
dimensions raises and requires dropping the table and re-ingesting. `search` modes:
`vector`, `keyword`, and `hybrid` (default; Reciprocal Rank Fusion, k=60, over both).

**Embeddings**: `embeddings.get_embedding_provider()` picks `local`
(sentence-transformers, optional `[local]` extra, GPU with VRAM check) or `api` (any
OpenAI-compatible endpoint) from `EMBEDDING_PROVIDER`. Providers expose `name`,
`dimensions`, `async embed(texts)`; `tests/fakes.HashEmbeddingProvider` is the test double.

**Background jobs (`jobs.py`)**: in-memory `JobRegistry` of asyncio tasks (lost on
restart, capped at 100). `ingest_or_submit` is shared by the MCP tool and `POST /ingest`
to choose sync vs. `202 + /jobs/{id}` polling.

**Other paths**: `llm.answer_question` does retrieval + an OpenAI-compatible chat
completion (`LLM_*` settings) for `/llm-chat`. `POST /ingest-deps` parses
requirements/pyproject/package(-lock) files (`parsers.py`), resolves doc URLs via
PyPI/npm registries (`doc_finder.py`), then submits crawl jobs.

**Config (`config.py`)**: pydantic-settings reads `~/.fathom-mcp/.env` then the repo
`.env` (later wins). `.env.example` is the reference for all keys.

## npm distribution

`npm/` is the `@fathom-mcp/server` package: `bin/docs-mcp-server.js` copies the bundled
`npm/python/` into `~/.fathom-mcp/src`, creates a venv there, and starts Postgres via
docker compose. `npm/python/src/docs_mcp` is a hand-maintained copy of `src/docs_mcp` with
no sync script, and it currently lags behind — edits to `src/` do not reach the npm
package unless copied over deliberately.
