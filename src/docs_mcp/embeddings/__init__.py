def get_embedding_provider():
    from docs_mcp.embeddings.local_provider import LocalEmbeddingProvider
    return LocalEmbeddingProvider()
