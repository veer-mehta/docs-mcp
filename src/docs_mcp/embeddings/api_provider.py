import logging

import httpx

from docs_mcp.config import settings

logger = logging.getLogger(__name__)


class APIEmbeddingProvider:
    def __init__(self) -> None:
        self._api_key = settings.embedding_api_key
        self._base_url = settings.embedding_base_url.rstrip("/")
        self._model = settings.embedding_model
        self._dims = settings.embedding_dims
        if not self._api_key:
            raise ValueError("EMBEDDING_API_KEY is required for API embedding provider")
        if not self._model:
            raise ValueError("EMBEDDING_MODEL is required for API embedding provider")
        logger.info("using API embedding provider: %s (%d dims)", self._model, self._dims)

    @property
    def name(self) -> str:
        return f"api:{self._model}"

    @property
    def dimensions(self) -> int:
        return self._dims

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self._base_url}/embeddings",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"model": self._model, "input": texts, "dimensions": self._dims},
            )
            resp.raise_for_status()
            data = resp.json()
            return [item["embedding"] for item in sorted(data["data"], key=lambda x: x["index"])]
