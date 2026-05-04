"""Ollama embedding provider — uses nomic-embed-text via local Ollama server."""

from __future__ import annotations

from ghost_pulse.rag.embeddings import EmbeddingProvider

_DEFAULT_MODEL = "nomic-embed-text"
_DEFAULT_HOST = "http://localhost:11434"
_DIMENSION = 768


class OllamaEmbeddingProvider(EmbeddingProvider):
    """Embeddings via a locally running Ollama server."""

    def __init__(self, host: str = _DEFAULT_HOST, model: str = _DEFAULT_MODEL) -> None:
        self._host = host.rstrip("/")
        self._model_name = model

    def _call(self, prompt: str) -> list[float]:
        import httpx
        resp = httpx.post(
            f"{self._host}/api/embeddings",
            json={"model": self._model_name, "prompt": prompt},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["embedding"]

    def embed(self, text: str) -> list[float]:
        return self._call(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._call(t) for t in texts]

    def is_available(self) -> bool:
        try:
            import httpx
            resp = httpx.get(f"{self._host}/api/tags", timeout=3)
            if resp.status_code == 200:
                models = [m["name"] for m in resp.json().get("models", [])]
                return any(self._model_name in m for m in models)
        except Exception:
            pass
        return False

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def dimension(self) -> int:
        return _DIMENSION
