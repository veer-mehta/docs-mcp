# docs-mcp

MCP server for documentation RAG. Crawl docs sites, upload files, or index
local folders — then semantic search and chat with your docs via any MCP client.

## Install

```bash
npx @docs-mcp/server
```

First run installs Python dependencies (~5GB, one-time). Subsequent runs start
instantly.

Requires **Python 3.12+** on your system.

## Quick start

```bash
# Start the server (stdio MCP transport)
npx @docs-mcp/server

# Or start the REST API + web UI
npx @docs-mcp/server --api
```

## Configuration

Create `~/.docs-mcp/.env` with at minimum:

```
LLM_API_KEY=your-key-here
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
LLM_MODEL=gemini-3.6-flash
DATABASE_URL=postgresql://docs_mcp:docs_mcp@localhost:5432/docs_mcp
```

Requires a running Postgres instance with pgvector:

```bash
docker run -d --name docs-mcp-postgres \
  -e POSTGRES_USER=docs_mcp -e POSTGRES_PASSWORD=docs_mcp \
  -e POSTGRES_DB=docs_mcp -p 5432:5432 \
  pgvector/pgvector:pg16
```

## MCP tools

| Tool | Description |
|---|---|
| `add_documentation` | Crawl a docs site and index it |
| `search_documentation` | Semantic search over indexed docs |
| `list_sources` | List all indexed sources |
| `get_ingest_status` | Check background crawl progress |
| `add_local_docs` | Index a local folder of docs |

## OpenCode integration

Add to `~/.config/opencode/opencode.jsonc`:

```json
{
  "mcp": {
    "docs-mcp": {
      "type": "local",
      "command": ["npx", "-y", "@docs-mcp/server"]
    }
  }
}
```

## REST API

When run with `--api`, serves at `http://127.0.0.1:8000`:

- `GET /search?q=...` — semantic search
- `GET /sources` — list indexed sources
- `POST /upload` — upload files (multipart)
- `POST /upload-folder` — index a local folder
- `GET /llm-chat?q=...` — chat with docs
- `GET /docs` — interactive OpenAPI documentation

## License

MIT
