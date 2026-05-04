"""Embedding provider factory — selects provider based on config."""

from __future__ import annotations

from typing import Any

from ghost_pulse.rag.embeddings import EmbeddingProvider, NullEmbeddingProvider


def get_embedding_provider(cfg: dict[str, Any]) -> EmbeddingProvider:
    """Return the best available embedding provider for the given config.

    Priority order (if provider is set to 'auto'):
      1. local (sentence-transformers) — free, fully offline
      2. ollama — local server
      3. openai — cloud
      4. none — RAG features disabled

    When provider is explicitly set, that provider is used.
    """
    rag_cfg = cfg.get("rag", {})
    embed_cfg = rag_cfg.get("embedding", {})
    provider_name = embed_cfg.get("provider", "auto").lower()

    if provider_name == "local":
        return _make_local(embed_cfg)
    if provider_name == "ollama":
        return _make_ollama(cfg, embed_cfg)
    if provider_name == "openai":
        return _make_openai(cfg, embed_cfg)
    if provider_name == "none":
        return NullEmbeddingProvider()

    # auto — try in order
    local = _make_local(embed_cfg)
    if local.is_available():
        return local

    ollama = _make_ollama(cfg, embed_cfg)
    if ollama.is_available():
        return ollama

    openai_p = _make_openai(cfg, embed_cfg)
    if openai_p.is_available():
        return openai_p

    return NullEmbeddingProvider()


def _make_local(embed_cfg: dict) -> EmbeddingProvider:
    from ghost_pulse.rag.embed_local import LocalEmbeddingProvider
    model = embed_cfg.get("local_model", "all-MiniLM-L6-v2")
    return LocalEmbeddingProvider(model=model)


def _make_ollama(cfg: dict, embed_cfg: dict) -> EmbeddingProvider:
    from ghost_pulse.rag.embed_ollama import OllamaEmbeddingProvider
    host = embed_cfg.get("ollama_host") or cfg.get("llm", {}).get("ollama", {}).get("host", "http://localhost:11434")
    model = embed_cfg.get("ollama_model", "nomic-embed-text")
    return OllamaEmbeddingProvider(host=host, model=model)


def _make_openai(cfg: dict, embed_cfg: dict) -> EmbeddingProvider:
    from ghost_pulse.rag.embed_openai import OpenAIEmbeddingProvider
    api_key = embed_cfg.get("openai_api_key") or cfg.get("llm", {}).get("openai", {}).get("api_key", "")
    model = embed_cfg.get("openai_model", "text-embedding-3-small")
    base_url = embed_cfg.get("openai_base_url") or cfg.get("llm", {}).get("openai", {}).get("base_url", "")
    return OpenAIEmbeddingProvider(api_key=api_key, model=model, base_url=base_url)
