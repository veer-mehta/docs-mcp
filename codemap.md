# Repository Atlas: docs-mcp

## Project Responsibility
A documentation RAG system: crawl framework docs via Scrapy+Playwright, chunk + embed with local HuggingFace models (BAAI/bge-m3), store in Postgres+pgvector, and expose semantic search via MCP server, REST API, and a web UI with LLM-powered chat (any OpenAI-compatible provider).

## System Entry Points
- `src/docs_mcp/api.py` — Starlette REST API + web UI server (`docs-mcp-api` CLI)
- `src/docs_mcp/server.py` — MCP server (`docs-mcp-server` CLI)
- `src/docs_mcp/scraper/runner.py` — Crawler CLI (`docs-mcp-crawl`)
- `docker-compose.yml` — Postgres+pgvector database
- `.env` — All configuration (DB, embedding, LLM, crawl settings)

## Directory Map
| Directory | Responsibility | Map |
|-----------|---------------|-----|
| `src/docs_mcp/` | Core orchestration: API, MCP server, config, LLM, jobs, pipeline | [View](src/docs_mcp/codemap.md) |
| `src/docs_mcp/embeddings/` | Local HuggingFace embedding generation with CUDA/CPU fallback | [View](src/docs_mcp/embeddings/codemap.md) |
| `src/docs_mcp/processing/` | HTML→markdown extraction, heading-aware chunking | [View](src/docs_mcp/processing/codemap.md) |
| `src/docs_mcp/scraper/` | Scrapy+Playwright crawler with file-based HTML cache | [View](src/docs_mcp/scraper/codemap.md) |
| `src/docs_mcp/storage/` | Postgres+pgvector schema, upsert, hybrid RRF search | [View](src/docs_mcp/storage/codemap.md) |

## Key Architecture
- **Crawl**: Scrapy spider → Playwright renders JS → HTML cached to disk → JSONL stdout
- **Process**: trafilatura extracts markdown → heading-aware chunker (3500 chars, 250 overlap)
- **Embed**: Local sentence-transformers (BAAI/bge-m3, 1024-dim) with CUDA/CPU auto-detect
- **Store**: asyncpg → Postgres+pgvector (HNSW cosine index + GIN FTS index)
- **Search**: Hybrid RRF fusion of vector similarity + keyword BM25
- **MCP**: 4 tools (add_documentation, search_documentation, list_sources, get_ingest_status)
- **Chat**: Intent detection → ingest or RAG → LLM response via any OpenAI-compatible API
- **UI**: Two-tab web interface (chat + search/sources), localStorage history
