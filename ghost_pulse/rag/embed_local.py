"""Local embedding provider using sentence-transformers (all-MiniLM-L6-v2)."""

from __future__ import annotations

from ghost_pulse.rag.embeddings import EmbeddingProvider

_MODEL_NAME = "all-MiniLM-L6-v2"
_DIMENSION = 384


class LocalEmbeddingProvider(EmbeddingProvider):
    """Uses sentence-transformers for fully offline, free embeddings."""

    def __init__(self, model: str = _MODEL_NAME) -> None:
        self._model_name = model
        self._model = None  # lazy load

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self._model_name)
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers not installed. "
                "Run: pip install 'ghost-pulse[embeddings]'"
            ) from exc

    def embed(self, text: str) -> list[float]:
        self._load()
        vec = self._model.encode([text], show_progress_bar=False)[0]
        return vec.tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self._load()
        vecs = self._model.encode(texts, show_progress_bar=False)
        return [v.tolist() for v in vecs]

    def is_available(self) -> bool:
        try:
            import sentence_transformers  # noqa: F401
            return True
        except ImportError:
            return False

    @property
    def name(self) -> str:
        return "local"

    @property
    def dimension(self) -> int:
        return _DIMENSION
