import asyncio
import logging

from docs_mcp.config import settings

logger = logging.getLogger(__name__)


class LocalEmbeddingProvider:
    def __init__(self) -> None:
        from sentence_transformers import SentenceTransformer

        device = self._resolve_device(settings.local_embedding_device)
        logger.info("loading %s on %s", settings.local_embedding_model, device)
        try:
            self._model = SentenceTransformer(
                settings.local_embedding_model, device=device
            )
        except Exception as exc:
            if device == "cpu":
                raise
            logger.warning("loading on %s failed (%s); retrying on CPU", device, exc)
            self._model = SentenceTransformer(
                settings.local_embedding_model, device="cpu"
            )
        # Chunker caps chunks well below this; the model default (8192 for
        # bge-m3) makes activation memory explode on small GPUs.
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
        logger.warning(
            "only %.1f GiB free on CUDA (<%.1f GiB needed); using CPU",
            free_bytes / 1024**3,
            needed / 1024**3,
        )
        return "cpu"

    @property
    def name(self) -> str:
        return f"local:{settings.local_embedding_model}"

    @property
    def dimensions(self) -> int:
        get_dimension = getattr(
            self._model, "get_embedding_dimension", None
        ) or self._model.get_sentence_embedding_dimension
        return int(get_dimension())

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self._encode, texts)

    def _encode(self, texts: list[str]) -> list[list[float]]:
        try:
            return self._run(texts)
        except _cuda_oom_error():
            logger.warning(
                "CUDA out of memory embedding %d texts; falling back to CPU",
                len(texts),
            )
            _empty_cache()
            return self._run(texts, device="cpu")

    def _run(
        self, texts: list[str], device: str | None = None
    ) -> list[list[float]]:
        kwargs = {"device": device} if device else {}
        return self._model.encode(
            texts, normalize_embeddings=True, show_progress_bar=False, **kwargs
        ).tolist()


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
