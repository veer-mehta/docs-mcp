# src/docs_mcp/storage/ - Code Map

## Responsibility
This directory provides Postgres+pgvector persistence for the docs-rag project. It handles:
- Database schema management with vector extension and indexes
- Upsert operations for document chunks with conflict resolution
- Hybrid search combining vector similarity and keyword search using RRF
- Source management including hash tracking and stale page pruning
- Connection pooling and lifecycle management

## Design Patterns
- **asyncpg connection pool**: Lazy initialization with min/max size (1/4) for efficient connection reuse
- **HNSW vector index**: Cosine similarity search on embedding vectors for fast nearest neighbor queries
- **GIN FTS index**: Full-text search on content using English language stemming
- **Hybrid RRF search**: Reciprocal Rank Fusion algorithm combining vector and keyword results with configurable modes (hybrid/vector/keyword)

## Data & Control Flow
1. **Schema init** (`ensure_schema`):
   - Creates vector extension if missing
   - Creates documents table with vector column
   - Creates HNSW index on embedding column
   - Creates GIN index on content for FTS
   - Validates embedding dimension matches expected

2. **Upsert** (`upsert_chunks`):
   - Accepts list of document chunks with metadata
   - Converts vectors to PostgreSQL vector literals
   - Uses INSERT ... ON CONFLICT DO UPDATE for idempotent updates
   - Batch executes via executemany for efficiency

3. **Search** (`search`):
   - Supports three modes: hybrid (default), vector-only, keyword-only
   - Hybrid mode: fetches both vector and keyword results, applies RRF
   - Vector search: cosine similarity using HNSW index
   - Keyword search: BM25 ranking using GIN index
   - Filters by source_id pattern when provided

4. **Prune** (`delete_stale_pages`):
   - Removes chunks from a source not present in latest crawl
   - Uses URL set comparison for efficient delta detection

## Integration Points
- **Dependencies**: asyncpg (connection pool), pgvector extension, PostgreSQL with HNSW/GIN support
- **Consumers**: 
  - `src/docs_mcp/ingester.py` - Calls upsert_chunks and ensure_schema
  - `src/docs_mcp/search.py` - Uses search, get_source_hashes, delete_stale_pages
  - `src/docs_mcp/api.py` - Manages database lifecycle and source operations
- **Configuration**: DSN connection string, table name (default: "documents"), embedding dimension
- **External interfaces**: Public methods include ensure_schema, upsert_chunks, search, get_source_hashes, delete_stale_pages, list_sources, delete_source, drop_table