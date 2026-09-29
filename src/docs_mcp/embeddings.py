import asyncio
import logging
from functools import cache

import httpx

from docs_mcp import settings

logger = logging.getLogger(__name__)


class LocalEmbeddingProvider:
    def __init__(self) -> None:
        from sentence_transformers import SentenceTransformer

        device = self._resolve_device(settings.local_embedding_device)
        logger.info("loading %s on %s", settings.local_embedding_model, device)
        try:
            self._model = SentenceTransformer(settings.local_embedding_model, device=device)
        except Exception as exc:
            if device == "cpu":
                raise
            logger.warning("loading on %s failed (%s); retrying on CPU", device, exc)
            self._model = SentenceTransformer(settings.local_embedding_model, device="cpu")
        self._model.max_seq_length = settings.local_embedding_max_tokens

    @staticmethod
    def _resolve_device(preference: str) -> str:
        import torch

        if preference in ("cpu", "cuda"):
            return preference
        if not torch.cuda.is_available():
            return "cpu"
        try:
            free_bytes, _total = torch.cuda.mem_get_info()
        except Exception:
            return "cuda"
        needed = int(settings.local_embedding_min_free_vram_gib * 1024**3)
        if free_bytes >= needed:
            return "cuda"
        logger.warning("only %.1f GiB free on CUDA (<%.1f GiB needed); using CPU", free_bytes / 1024**3, needed / 1024**3)
        return "cpu"

    @property
    def name(self) -> str:
        return f"local:{settings.local_embedding_model}"

    @property
    def dimensions(self) -> int:
        get_dimension = getattr(self._model, "get_embedding_dimension", None) or self._model.get_sentence_embedding_dimension
        return int(get_dimension())

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self._run, texts)

    def _run(self, texts: list[str], device: str | None = None) -> list[list[float]]:
        kwargs = {"device": device} if device else {}
        try:
            return self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False, **kwargs).tolist()
        except _cuda_oom_error():
            logger.warning("CUDA out of memory embedding %d texts; falling back to CPU", len(texts))
            _empty_cache()
            return self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False, device="cpu").tolist()


def _cuda_oom_error() -> type[Exception]:
    try:
        import torch

        return torch.cuda.OutOfMemoryError
    except ImportError:
        return RuntimeError


def _empty_cache() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


MAX_RETRIES = 5
INITIAL_BACKOFF = 4.0


class RemoteEmbeddingProvider:
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
        backoff = INITIAL_BACKOFF
        for attempt in range(MAX_RETRIES):
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(f"{self._base_url}/embeddings", headers={"Authorization": f"Bearer {self._api_key}"}, json={"model": self._model, "input": texts, "dimensions": self._dims})
                if resp.status_code == 429:
                    retry_after = resp.headers.get("retry-after")
                    wait = float(retry_after) if retry_after else backoff
                    logger.warning("rate limited (429), retrying in %.1fs (attempt %d/%d)", wait, attempt + 1, MAX_RETRIES)
                    await asyncio.sleep(wait)
                    backoff *= 2
                    continue
                resp.raise_for_status()
                data = resp.json()
                return [item["embedding"] for item in sorted(data["data"], key=lambda x: x["index"])]
        raise RuntimeError(f"embedding failed after {MAX_RETRIES} retries due to rate limiting")


@cache
def get_embedding_provider():
    if settings.embedding_provider == "api":
        return RemoteEmbeddingProvider()
    return LocalEmbeddingProvider()
