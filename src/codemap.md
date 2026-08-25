# src/

## Responsibility

Contains `docs_mcp`, the main Python package for the documentation RAG system. Provides crawl → embed → store → search → chat capabilities.

## Directory Map

| Directory | Responsibility |
|-----------|----------------|
| `docs_mcp/` (root) | Core orchestration — API server, MCP server, config, LLM chat, job management, pipeline |
| `docs_mcp/embeddings/` | Text embedding generation via local HuggingFace models |
| `docs_mcp/processing/` | HTML extraction and heading-aware markdown chunking |
| `docs_mcp/scraper/` | Scrapy + Playwright web crawler with file-based HTML cache |
| `docs_mcp/storage/` | Postgres+pgvector persistence and hybrid search |
