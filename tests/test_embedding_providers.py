"""Tests for embedding providers (unit-level — no real model calls)."""

from __future__ import annotations

import pytest

from devpulse.rag.embeddings import NullEmbeddingProvider


# ------------------------------------------------------------------
# NullEmbeddingProvider
# ------------------------------------------------------------------

def test_null_provider_not_available():
    p = NullEmbeddingProvider()
    assert p.is_available() is False


def test_null_provider_returns_empty():
    p = NullEmbeddingProvider()
    assert p.embed("hello") == []
    assert p.embed_batch(["a", "b", "c"]) == [[], [], []]


def test_null_provider_name():
    assert NullEmbeddingProvider().name == "none"


def test_null_provider_dimension():
    assert NullEmbeddingProvider().dimension == 0


# ------------------------------------------------------------------
# LocalEmbeddingProvider — import check only (model may not be installed)
# ------------------------------------------------------------------

def test_local_provider_import():
    from devpulse.rag.embed_local import LocalEmbeddingProvider
    p = LocalEmbeddingProvider()
    assert p.name == "local"
    assert p.dimension == 384
    # is_available depends on whether sentence-transformers is installed
    # Just verify it doesn't raise
    available = p.is_available()
    assert isinstance(available, bool)


# ------------------------------------------------------------------
# OllamaEmbeddingProvider
# ------------------------------------------------------------------

def test_ollama_provider_defaults():
    from devpulse.rag.embed_ollama import OllamaEmbeddingProvider
    p = OllamaEmbeddingProvider()
    assert p.name == "ollama"
    assert p.dimension == 768


def test_ollama_provider_unavailable_when_no_server():
    from devpulse.rag.embed_ollama import OllamaEmbeddingProvider
    p = OllamaEmbeddingProvider(host="http://127.0.0.1:19999")  # nothing running
    assert p.is_available() is False


# ------------------------------------------------------------------
# OpenAIEmbeddingProvider
# ------------------------------------------------------------------

def test_openai_provider_unavailable_without_key():
    from devpulse.rag.embed_openai import OpenAIEmbeddingProvider
    p = OpenAIEmbeddingProvider(api_key="")
    # Available only if openai package present AND key is set
    # With empty key it should return False
    try:
        import openai  # noqa: F401
        assert p.is_available() is False
    except ImportError:
        assert p.is_available() is False


def test_openai_provider_name():
    from devpulse.rag.embed_openai import OpenAIEmbeddingProvider
    p = OpenAIEmbeddingProvider(api_key="sk-test")
    assert p.name == "openai"
    assert p.dimension == 1536


# ------------------------------------------------------------------
# embed_factory — auto selection
# ------------------------------------------------------------------

def test_factory_returns_null_when_nothing_available(monkeypatch):
    """When no providers are installed/configured, factory returns NullEmbeddingProvider."""
    from devpulse.rag import embed_factory

    # Monkeypatch all three make functions to return NullEmbeddingProvider
    monkeypatch.setattr(
        embed_factory, "_make_local",
        lambda cfg: NullEmbeddingProvider()
    )
    monkeypatch.setattr(
        embed_factory, "_make_ollama",
        lambda cfg, embed: NullEmbeddingProvider()
    )
    monkeypatch.setattr(
        embed_factory, "_make_openai",
        lambda cfg, embed: NullEmbeddingProvider()
    )

    cfg = {"rag": {"embedding": {"provider": "auto"}}}
    provider = embed_factory.get_embedding_provider(cfg)
    assert provider.name == "none"


def test_factory_respects_explicit_none():
    from devpulse.rag.embed_factory import get_embedding_provider
    cfg = {"rag": {"embedding": {"provider": "none"}}}
    p = get_embedding_provider(cfg)
    assert p.name == "none"
