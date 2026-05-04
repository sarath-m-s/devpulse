"""Tests for LLM provider layer — factory, fallback, graceful degradation."""

import pytest

from ghost_pulse.llm.base import LLMProvider, LLMResponse
from ghost_pulse.llm.factory import NoopProvider, get_provider, probe_all
from ghost_pulse.llm.ollama_provider import OllamaProvider
from ghost_pulse.llm.claude_provider import ClaudeProvider
from ghost_pulse.llm.openai_provider import OpenAIProvider
from ghost_pulse.llm.groq_provider import GroqProvider


class _FakeProvider(LLMProvider):
    def __init__(self, name_: str, available: bool) -> None:
        self._name = name_
        self._available = available
        self.calls: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    def is_available(self) -> bool:
        return self._available

    def analyze(self, prompt: str, system_prompt: str = "") -> LLMResponse:
        self.calls.append(prompt)
        return LLMResponse(content="ok", model="fake", provider=self._name)


class TestNoopProvider:
    def test_not_available(self):
        p = NoopProvider()
        assert not p.is_available()

    def test_analyze_returns_placeholder(self):
        p = NoopProvider()
        resp = p.analyze("anything")
        assert "not configured" in resp.content.lower()
        assert resp.provider == "none"


class TestProviderFactory:
    def test_returns_noop_when_no_provider_available(self, monkeypatch):
        for cls in (OllamaProvider, GroqProvider, ClaudeProvider, OpenAIProvider):
            monkeypatch.setattr(cls, "is_available", lambda self: False)
        cfg = {"llm": {"provider": "none"}}
        provider = get_provider(cfg)
        assert provider.name == "none"

    def test_respects_explicit_provider_config(self, monkeypatch):
        # Make Ollama report as available
        monkeypatch.setattr(OllamaProvider, "is_available", lambda self: True)
        cfg = {"llm": {"provider": "ollama", "ollama": {"host": "", "model": ""}}}
        provider = get_provider(cfg)
        assert provider.name == "ollama"

    def test_falls_back_to_auto_detect(self, monkeypatch):
        # Mark groq as available, everything else not
        monkeypatch.setattr(OllamaProvider, "is_available", lambda self: False)
        monkeypatch.setattr(GroqProvider, "is_available", lambda self: True)
        monkeypatch.setattr(ClaudeProvider, "is_available", lambda self: False)
        monkeypatch.setattr(OpenAIProvider, "is_available", lambda self: False)
        cfg = {"llm": {"provider": "claude", "groq": {"api_key": "fake", "model": ""}}}
        # Explicit claude not available → fall back to auto-detect → groq
        provider = get_provider(cfg)
        assert provider.name == "groq"

    def test_returns_noop_when_all_unavailable(self, monkeypatch):
        for cls in (OllamaProvider, GroqProvider, ClaudeProvider, OpenAIProvider):
            monkeypatch.setattr(cls, "is_available", lambda self: False)
        cfg = {"llm": {"provider": "ollama"}}
        provider = get_provider(cfg)
        assert provider.name == "none"


class TestOllamaProvider:
    def test_name(self):
        p = OllamaProvider()
        assert p.name == "ollama"

    def test_unavailable_when_server_down(self, monkeypatch):
        import httpx
        def _raise(*args, **kwargs):
            raise httpx.ConnectError("refused")
        monkeypatch.setattr(httpx, "get", _raise)
        p = OllamaProvider(host="http://127.0.0.1:19999")
        assert not p.is_available()


class TestClaudeProvider:
    def test_unavailable_without_api_key(self):
        p = ClaudeProvider(api_key="")
        assert not p.is_available()

    def test_raises_import_error_without_sdk(self, monkeypatch):
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "anthropic":
                raise ImportError("No module named 'anthropic'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        p = ClaudeProvider(api_key="fake-key")
        p._client = None  # reset cached client
        with pytest.raises(RuntimeError, match="anthropic package"):
            p._get_client()


class TestGroqProvider:
    def test_unavailable_without_api_key(self):
        p = GroqProvider(api_key="")
        assert not p.is_available()


class TestOpenAIProvider:
    def test_unavailable_without_api_key(self):
        p = OpenAIProvider(api_key="")
        assert not p.is_available()


class TestProbeAll:
    def test_returns_list_and_active(self, monkeypatch):
        monkeypatch.setattr(OllamaProvider, "is_available", lambda self: True)
        monkeypatch.setattr(ClaudeProvider, "is_available", lambda self: False)
        monkeypatch.setattr(OpenAIProvider, "is_available", lambda self: False)
        monkeypatch.setattr(GroqProvider, "is_available", lambda self: False)
        cfg = {"llm": {"provider": "ollama", "ollama": {"host": "", "model": ""}}}
        results, active = probe_all(cfg)
        assert isinstance(results, list)
        assert len(results) == 4
        assert active == "ollama"

    def test_active_is_none_when_all_unavailable(self, monkeypatch):
        for cls in (OllamaProvider, GroqProvider, ClaudeProvider, OpenAIProvider):
            monkeypatch.setattr(cls, "is_available", lambda self: False)
        cfg = {"llm": {"provider": "none"}}
        results, active = probe_all(cfg)
        assert active == "none"
