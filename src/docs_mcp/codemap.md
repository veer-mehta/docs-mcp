# Responsibility

The `src/docs_mcp/` directory contains the core implementation of a documentation Retrieval-Augmented Generation (RAG) system. It crawls framework documentation, processes and indexes content, provides semantic search capabilities, and exposes functionality through multiple interfaces: a REST API, an MCP (Model Context Protocol) server, and a web UI with LLM-powered chat. The system uses local HuggingFace embeddings stored in a Postgres+pgvector database.

## Design Patterns

- **Dataclasses**: Used extensively for structured data transfer (`IngestResult` in pipeline.py, `Job` in jobs.py, `ChatAction` in chat.py)
- **Async Context Managers**: Lifespan management in api.py (`lifespan` function) for startup/shutdown resource handling
- **Dependency Injection**: Database and job registry passed to functions rather than using globals directly
- **Factory Pattern**: `get_embedding_provider()` abstraction for embedding model selection
- **Queue-Based Worker Pattern**: McpBridge uses asyncio.Queue to manage MCP server subprocess communication
- **Registry Pattern**: `JOBS` global JobRegistry instance for in-memory job tracking
- **Strategy Pattern**: Search modes (hybrid/vector/keyword) in search_documentation tool
- **Builder Pattern**: MCP tool response construction with structured data formatting

## Data & Control Flow

1. **Ingestion Flow**:
   - User requests documentation ingestion via `/ingest` endpoint (api.py) or MCP `add_documentation` tool (server.py)
   - Request validated and passed to `submit_ingest()` (jobs.py) which creates a Job and starts async processing
   - Job runner calls `ingest_documentation()` (pipeline.py) which:
     - Launches subprocess crawler (`docs_mcp.scraper.runner`)
     - Processes crawled HTML → markdown → chunks
     - Computes content hashes for change detection
     - Embeds chunks via HuggingFace provider
     - Upserts to Postgres via Database interface
     - Returns IngestResult with metrics

2. **Search Flow**:
   - Search request via `/search` (api.py) or MCP `search_documentation` (server.py)
   - Query embedded via provider, searched against pgvector index
   - Results formatted as markdown blocks or JSON (via asdict)
   - MCP bridge translates between stdio MCP calls and HTTP API

3. **Chat Flow**:
   - LLM chat via `/llm-chat` (api.py) processes natural language queries
   - `detect_intent()` (chat.py) identifies ingestion requests vs. general questions
   - For ingestion intents: triggers crawl job via submit_ingest()
   - For chat intents: retrieves context via MCP search, generates LLM response with citations

4. **MCP Bridge Flow**:
   - McpBridge maintains persistent subprocess running docs_mcp.server
   - Tool calls queued and processed by dedicated worker task
   - stdio communication with MCP session initialized per worker lifetime
   - Response parsing converts MCP markdown/text to structured JSON

## Integration Points

**Internal Dependencies**:
- `docs_mcp.config`: Centralized settings (Pydantic BaseSettings)
- `docs_mcp.storage.db`: Database abstraction layer (Postgres+pgvector)
- `docs_mcp.embeddings`: HuggingFace embedding provider interface
- `docs_mcp.processing`: HTML→markdown conversion and text chunking
- `docs_mcp.scraper`: Documentation crawler (subprocess)

**External Dependencies**:
- PostgreSQL with pgvector extension for vector storage
- HuggingFace sentence-transformers for local embeddings
- MCP (Model Context Protocol) stdio server/client
- Starlette for lightweight ASPI web framework
- Uvicorn ASGI server
- Pydantic for settings management
- HTTPX for LLM API calls
- AsyncIO for concurrency

**Consumers**:
- REST API clients (web UI, external services)
- MCP clients (Claude Desktop, IDE integrations, custom agents)
- Direct Python imports (testing, scripting)
- Background job processors (async ingestion tasks)

**Key Interfaces**:
- Database: `ensure_schema()`, `search()`, `upsert_chunks()`, `list_sources()`, `get_source_hashes()`
- EmbeddingProvider: `embed()`, `name`, `dimensions`
- JobRegistry: `create()`, `get()`, `list()`
- McpBridge: `call()`, `close()`