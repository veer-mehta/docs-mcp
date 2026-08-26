import hashlib
import math


class HashEmbeddingProvider:
    """Deterministic fake embeddings for development and testing only."""

    name = "hash"
    dimensions = 1536

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        seed = int.from_bytes(digest[:8], "big")
        values = [math.sin(seed + i) for i in range(self.dimensions)]
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]
