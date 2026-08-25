# Embeddings Directory Codemap

## Responsibility
This directory handles text embedding generation for vector search in the docs-rag project. It provides a Protocol-based abstraction for embedding providers with two concrete implementations:
- A real implementation using sentence-transformers (local HuggingFace models) with CUDA/CPU fallback
- A deterministic fake implementation for testing and development

The directory encapsulates all embedding-related logic, allowing the rest of the application to work with embeddings through a consistent interface regardless of the underlying provider.

## Design Patterns
- **Protocol (Interface Segregation)**: The `EmbeddingProvider` Protocol in `base.py` defines the contract that all embedding providers must implement (`name`, `dimensions`, and `embed` method).
- **Factory Pattern**: The `get_embedding_provider()` function in `__init__.py` acts as a simple factory that instantiates the appropriate provider based on configuration (`settings.embedding_provider`).
- **CUDA/CPU Fallback**: The `LocalEmbeddingProvider` implements automatic fallback from CUDA to CPU when GPU memory is insufficient or CUDA is unavailable, with runtime device detection and OOM error handling.

## Data & Control Flow
1. **Configuration**: The application reads `settings.embedding_provider` to determine which provider to use.
2. **Factory Selection**: `get_embedding_provider()` returns either `LocalEmbeddingProvider` or `HashEmbeddingProvider` based on the configuration.
3. **Embedding Generation**: 
   - For `LocalEmbeddingProvider`: Texts are processed through a SentenceTransformer model, with automatic device selection (CUDA preferred, CPU fallback) and OOM handling.
   - For `HashEmbeddingProvider`: Texts are converted to deterministic vectors using SHA256 hashing and trigonometric functions (no semantic meaning).
4. **Async Interface**: All providers implement an async `embed()` method that accepts a list of strings and returns a list of embedding vectors (list of floats).
5. **Consumption**: Other modules call `get_embedding_provider().embed(texts)` to obtain embeddings for indexing or search.

## Integration Points
### Dependencies
- **sentence-transformers**: Used by `LocalEmbeddingProvider` for actual embedding generation (optional dependency, only required when using "local" provider).
- **torch**: Used by `LocalEmbeddingProvider` for CUDA device detection and memory management.
- **docs_mcp.config**: Accesses configuration settings for provider selection and model parameters.
- **typing.Protocol**: Defines the embedding provider interface.
- **asyncio**: Enables non-blocking embedding computation via `asyncio.to_thread()`.

### Consumers
- **docs_mcp.ingest**: Likely uses embeddings during document ingestion to create vector representations for storage.
- **docs_mcp.query**: Likely uses embeddings to convert search queries into vectors for similarity search.
- **tests**: The `HashEmbeddingProvider` is primarily used in test environments to provide deterministic, fast embeddings without requiring external models.

### Configuration
- `settings.embedding_provider`: Chooses between "local" and "hash" providers.
- `settings.local_embedding_model`: Specifies the sentence-transformers model to use (when provider="local").
- `settings.local_embedding_device`: Preferred device ("cpu", "cuda", or auto-detect).
- `settings.local_embedding_max_tokens`: Maximum sequence length for the embedding model.