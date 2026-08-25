from docs_mcp.config import settings
from docs_mcp.embeddings.base import EmbeddingProvider


def get_embedding_provider() -> EmbeddingProvider:
    provider = settings.embedding_provider.lower()
    if provider == "local":
        from docs_mcp.embeddings.local_provider import LocalEmbeddingProvider

        return LocalEmbeddingProvider()
    if provider == "hash":
        from docs_mcp.embeddings.hash_provider import HashEmbeddingProvider

        return HashEmbeddingProvider()
    raise ValueError(f"Unknown EMBEDDING_PROVIDER: {settings.embedding_provider}")
