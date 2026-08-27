# fathom-mcp

Documentation RAG system: crawl a docs site → chunk + embed locally (HuggingFace)
→ store in Postgres+pgvector → semantic search via MCP server, REST API, and web UI.

## Architecture

```mermaid
flowchart LR
    A[Browser] --> B[API Server]
    B --> D[Web UI]
    B --> C[(Postgres + pgvector)]
    B --> G[Ingestion Pipeline]
    G --> H[Scraper Subprocess]
    G --> C
    I[AI Client<br/>Claude, Cursor] -->|MCP over stdio| F[MCP Server]
    F --> C
```

## Quick start

```bash
git clone ... fathom-mcp && cd fathom-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[local]"
docker compose up -d
cp .env.example .env              # set LLM_API_KEY
.venv/bin/docs-mcp-api            # http://127.0.0.1:8000
```

## npm (no clone needed)

```bash
npx @fathom-mcp/server            # first run installs ~5GB deps, then instant
npx @fathom-mcp/server --api      # REST API + web UI
```

## MCP tools

`add_documentation` · `search_documentation` · `list_sources` · `get_ingest_status` · `add_local_docs`

## REST API

| Endpoint | What |
|---|---|
| `GET /search?q=...` | Semantic search |
| `GET /sources` | Indexed sources |
| `POST /upload` | Upload files |
| `POST /upload-folder` | Index a local folder |
| `GET /llm-chat?q=...` | Chat with docs |
| `GET /about` | System info |

## OpenCode

Add to `~/.config/opencode/opencode.jsonc`:

```json
{
  "mcp": {
    "fathom-mcp": {
      "type": "local",
      "command": ["npx", "-y", "@fathom-mcp/server"]
    }
  }
}
```

## Config

`~/.fathom-mcp/.env` — `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `DATABASE_URL`.
