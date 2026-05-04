"""LLM provider factory — selects and instantiates the right provider."""

from __future__ import annotations

from typing import Any

from ghost_pulse.llm.base import LLMProvider
from ghost_pulse.llm.ollama_provider import OllamaProvider
from ghost_pulse.llm.claude_provider import ClaudeProvider
from ghost_pulse.llm.openai_provider import OpenAIProvider
from ghost_pulse.llm.groq_provider import GroqProvider


class NoopProvider(LLMProvider):
    """Placeholder when no LLM is configured — all methods are no-ops."""

    @property
    def name(self) -> str:
        return "none"

    def is_available(self) -> bool:
        return False

    def analyze(self, prompt: str, system_prompt: str = "") -> "LLMResponse":  # type: ignore[name-defined]
        from ghost_pulse.llm.base import LLMResponse
        return LLMResponse(
            content="[LLM not configured. Run: ghost config providers]",
            model="none",
            provider="none",
        )


def _build(name: str, llm_cfg: dict[str, Any]) -> LLMProvider:
    if name == "ollama":
        c = llm_cfg.get("ollama", {})
        return OllamaProvider(host=c.get("host", ""), model=c.get("model", ""))
    if name == "claude":
        c = llm_cfg.get("claude", {})
        return ClaudeProvider(api_key=c.get("api_key", ""), model=c.get("model", ""))
    if name == "openai":
        c = llm_cfg.get("openai", {})
        return OpenAIProvider(
            api_key=c.get("api_key", ""),
            model=c.get("model", ""),
            base_url=c.get("base_url", ""),
        )
    if name == "groq":
        c = llm_cfg.get("groq", {})
        return GroqProvider(api_key=c.get("api_key", ""), model=c.get("model", ""))
    raise ValueError(f"Unknown provider: {name}")


def get_provider(config: dict[str, Any]) -> LLMProvider:
    """
    Return the best available LLM provider.

    Priority:
    1. Explicitly configured provider in config["llm"]["provider"]
    2. Auto-detect: Ollama → Groq → Claude → OpenAI
    """
    llm_cfg = config.get("llm", {})
    configured = llm_cfg.get("provider", "").lower()

    if configured and configured != "none":
        try:
            provider = _build(configured, llm_cfg)
            if provider.is_available():
                return provider
        except Exception:
            pass

    # Auto-detect fallback
    for name in ("ollama", "groq", "claude", "openai"):
        try:
            p = _build(name, llm_cfg)
            if p.is_available():
                return p
        except Exception:
            continue

    return NoopProvider()


def probe_all(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Test all providers and return their status."""
    llm_cfg = config.get("llm", {})
    results = []
    for name in ("ollama", "claude", "openai", "groq"):
        try:
            p = _build(name, llm_cfg)
            available = p.is_available()
        except Exception:
            available = False

        cfg_section = llm_cfg.get(name, {})
        model = cfg_section.get("model", "—")
        results.append({"provider": name, "available": available, "model": model})

    active = get_provider(config)
    return results, active.name  # type: ignore[return-value]
