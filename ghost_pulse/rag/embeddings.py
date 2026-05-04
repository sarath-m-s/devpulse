"""Abstract embedding provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class EmbeddingProvider(ABC):
    """Base class for all embedding providers."""

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Return embedding vector for a single text."""

    @abstractmethod
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return embedding vectors for a list of texts."""

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this provider is configured and reachable."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier string."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Dimensionality of produced embeddings."""


class NullEmbeddingProvider(EmbeddingProvider):
    """Fallback provider — always returns empty vectors. RAG features degrade gracefully."""

    def embed(self, text: str) -> list[float]:
        return []

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[] for _ in texts]

    def is_available(self) -> bool:
        return False

    @property
    def name(self) -> str:
        return "none"

    @property
    def dimension(self) -> int:
        return 0
