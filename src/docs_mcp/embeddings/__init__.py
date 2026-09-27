from functools import cache


@cache
def get_embedding_provider():
    from docs_mcp.config import settings

    if settings.embedding_provider == "api":
        from docs_mcp.embeddings.remote import RemoteEmbeddingProvider

        return RemoteEmbeddingProvider()
    from docs_mcp.embeddings.local import LocalEmbeddingProvider

    return LocalEmbeddingProvider()
