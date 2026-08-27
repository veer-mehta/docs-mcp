def get_embedding_provider():
    from docs_mcp.config import settings
    if settings.embedding_provider == "api":
        from docs_mcp.embeddings.api_provider import APIEmbeddingProvider
        return APIEmbeddingProvider()
    from docs_mcp.embeddings.local_provider import LocalEmbeddingProvider
    return LocalEmbeddingProvider()
