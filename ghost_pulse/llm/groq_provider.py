"""Groq LLM provider — fast inference, generous free tier."""

from __future__ import annotations

import os
from typing import Any

from devpulse.llm.base import LLMProvider, LLMResponse


class GroqProvider(LLMProvider):
    """Uses the Groq SDK for fast cloud inference."""

    DEFAULT_MODEL = "llama-3.1-70b-versatile"

    def __init__(self, api_key: str = "", model: str = "") -> None:
        self._api_key = api_key or os.environ.get("GROQ_API_KEY", "")
        self._model = model or self.DEFAULT_MODEL
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from groq import Groq
                self._client = Groq(api_key=self._api_key)
            except ImportError as exc:
                raise RuntimeError(
                    "groq package not installed. Run: pip install 'devpulse[groq]'"
                ) from exc
        return self._client

    @property
    def name(self) -> str:
        return "groq"

    def is_available(self) -> bool:
        if not self._api_key:
            return False
        try:
            client = self._get_client()
            client.models.list()
            return True
        except Exception:
            return False

    def analyze(self, prompt: str, system_prompt: str = "") -> LLMResponse:
        client = self._get_client()
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = client.chat.completions.create(
            model=self._model,
            messages=messages,
            max_tokens=1024,
        )
        content = response.choices[0].message.content or ""
        tokens = response.usage.total_tokens if response.usage else None
        return LLMResponse(
            content=content,
            model=self._model,
            provider=self.name,
            tokens_used=tokens,
            cost_estimate=0.0,
        )
