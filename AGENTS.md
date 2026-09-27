# fathom-mcp

Documentation RAG system: crawl a docs site → chunk + embed locally (HuggingFace)
→ store in Postgres+pgvector → semantic search via MCP server, REST API, and web UI.

## Commands

- Tests: `.venv/bin/pytest` (`tests/test_store.py` is marked `integration` and needs the
  compose Postgres; skip it with `-m "not integration"`)
- API + web UI: `docs-mcp-api` → http://127.0.0.1:8000
- MCP server: `docs-mcp-server` (stdio)
- Crawler CLI: `docs-mcp-crawl --url <url>`

Configuration lives in `.env` (see `.env.example`). Architecture is described in
`README.md`.

## Verify

- Smoke test: start `docs-mcp-api`, wait ~25s for the model to load, then curl
  `/sources`, `/search?q=...`, `/llm-chat?q=...`.
- Stop it by port — `pkill -f` matches its own shell wrapper and kills the caller:
  `PID=$(ss -tlnp | grep :8000 | grep -oP 'pid=\K[0-9]+' | head -1); kill $PID`
- MCP stdio: pipe `initialize`, `notifications/initialized`, `tools/list` as
  JSON-RPC lines into `docs-mcp-server`. The notification is required; order matters.
- Inline JS in `static/index.html` has no build step: extract the `<script>`
  blocks and `node --check` them.
- `node scripts/check_sanitizer.mjs` after touching `clean()` — it is the only
  thing between LLM-authored markdown and `innerHTML`. Needs jsdom via
  `JSDOM_PATH`; deliberately not a project dependency.

## Conventions

No comments or docstrings unless their absence causes real damage. Intentional
exceptions: the GPU-OOM note in `embeddings/local.py`, the
prune-only-on-clean-crawl warning in `pipeline.py`, and the MCP tool docstrings in
`adapters/mcp_server.py` — those are protocol payloads sent to clients, not documentation.

## Agent skills

### Issue tracker

Issues live in GitHub Issues for `veer-mehta/fathom-mcp`, managed with the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one root `CONTEXT.md` plus `docs/adr/`, created lazily. See `docs/agents/domain.md`.
