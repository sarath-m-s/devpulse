"""OpenAI embedding provider — uses text-embedding-3-small."""

from __future__ import annotations

from devpulse.rag.embeddings import EmbeddingProvider

_DEFAULT_MODEL = "text-embedding-3-small"
_DIMENSION = 1536


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """Embeddings via OpenAI API (cloud, paid)."""

    def __init__(self, api_key: str = "", model: str = _DEFAULT_MODEL, base_url: str = "") -> None:
        self._api_key = api_key
        self._model_name = model
        self._base_url = base_url
        self._client = None  # lazy

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            import openai
            kwargs: dict = {"api_key": self._api_key}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = openai.OpenAI(**kwargs)
            return self._client
        except ImportError as exc:
            raise RuntimeError(
                "openai package not installed. Run: pip install 'devpulse[openai]'"
            ) from exc

    def embed(self, text: str) -> list[float]:
        client = self._get_client()
        resp = client.embeddings.create(input=[text], model=self._model_name)
        return resp.data[0].embedding

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        client = self._get_client()
        resp = client.embeddings.create(input=texts, model=self._model_name)
        return [d.embedding for d in sorted(resp.data, key=lambda x: x.index)]

    def is_available(self) -> bool:
        try:
            import openai  # noqa: F401
            return bool(self._api_key)
        except ImportError:
            return False

    @property
    def name(self) -> str:
        return "openai"

    @property
    def dimension(self) -> int:
        return _DIMENSION
