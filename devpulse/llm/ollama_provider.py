"""Ollama local LLM provider — free, fully local, no API key required."""

from __future__ import annotations

from typing import Any

import httpx

from devpulse.llm.base import LLMProvider, LLMResponse


class OllamaProvider(LLMProvider):
    """Calls a locally running Ollama server."""

    DEFAULT_MODEL = "llama3.2:3b"
    DEFAULT_HOST = "http://localhost:11434"

    def __init__(self, host: str = "", model: str = "") -> None:
        self._host = (host or self.DEFAULT_HOST).rstrip("/")
        self._model = model or self.DEFAULT_MODEL

    @property
    def name(self) -> str:
        return "ollama"

    def is_available(self) -> bool:
        try:
            resp = httpx.get(f"{self._host}/api/tags", timeout=3)
            return resp.status_code == 200
        except Exception:
            return False

    def analyze(self, prompt: str, system_prompt: str = "") -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
        }
        if system_prompt:
            payload["system"] = system_prompt

        resp = httpx.post(
            f"{self._host}/api/generate",
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data.get("response", "")
        return LLMResponse(
            content=content,
            model=self._model,
            provider=self.name,
            tokens_used=data.get("eval_count"),
            cost_estimate=0.0,
        )
