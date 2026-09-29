# docs-mcp

Documentation RAG system: crawl a docs site → chunk + embed locally (HuggingFace)
→ store in Postgres+pgvector → semantic search via MCP server, REST API, and web UI.

## Demo
<img width="2559" height="1487" alt="image" src="https://github.com/user-attachments/assets/0951996f-7b78-487c-bf04-6d965198e589" />

live at https://docs-mcp.veermehta.dev

## Architecture

```mermaid
flowchart LR
    A[Browser] --> B[API Server]
    B --> D[Web UI]
    B --> C[(Postgres + pgvector)]
    B --> G[Ingestion Pipeline]
    G --> H[Scraper Subprocess]
    G --> C
    I[AI Client<br/>Claude Code, OpenCode] -->|MCP over stdio| F[MCP Server]
    F --> C
```

## Quick start

```bash
git clone ... docs-mcp && cd docs-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[local]"
docker compose up -d
cp .env.example .env              # set LLM_API_KEY
.venv/bin/docs-mcp-api            # http://127.0.0.1:8000
```

## npm (no clone needed)

```bash
npx @docs-mcp/server            # first run installs ~5GB deps, then instant
npx @docs-mcp/server --api      # REST API + web UI
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
| `GET /docs` | Interactive OpenAPI documentation |
| `GET /openapi.json` | OpenAPI schema |

## OpenCode

Add to `~/.config/opencode/opencode.jsonc`:

```json
{
  "mcp": {
    "docs-mcp": {
      "type": "local",
      "command": ["/path/to/docs-mcp/.venv/bin/python", "-m", "docs_mcp.mcp"]
    }
  }
}
```

Replace `/path/to/docs-mcp` with your actual clone path. You can verify it works with:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' | /path/to/docs-mcp/.venv/bin/python -m docs_mcp.mcp
```

## Config

`~/.docs-mcp/.env` — set `EMBEDDING_PROVIDER=api` + `EMBEDDING_API_KEY` for remote embeddings (Jina, OpenAI, etc.), or leave as `local` for HuggingFace.
