"""Anthropic Claude LLM provider."""

from __future__ import annotations

import os
from typing import Any

from devpulse.llm.base import LLMProvider, LLMResponse


class ClaudeProvider(LLMProvider):
    """Uses the Anthropic SDK to call Claude models."""

    DEFAULT_MODEL = "claude-sonnet-4-20250514"

    def __init__(self, api_key: str = "", model: str = "") -> None:
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._model = model or self.DEFAULT_MODEL
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self._api_key)
            except ImportError as exc:
                raise RuntimeError(
                    "anthropic package not installed. Run: pip install 'devpulse[claude]'"
                ) from exc
        return self._client

    @property
    def name(self) -> str:
        return "claude"

    def is_available(self) -> bool:
        if not self._api_key:
            return False
        try:
            client = self._get_client()
            # Lightweight probe — list models endpoint
            client.models.list(limit=1)
            return True
        except Exception:
            return False

    def analyze(self, prompt: str, system_prompt: str = "") -> LLMResponse:
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        response = client.messages.create(**kwargs)
        content = response.content[0].text
        tokens = (response.usage.input_tokens or 0) + (response.usage.output_tokens or 0)
        # Rough cost: $3/$15 per MTok (input/output) for Sonnet
        cost = (
            response.usage.input_tokens * 3e-6
            + response.usage.output_tokens * 15e-6
        )
        return LLMResponse(
            content=content,
            model=self._model,
            provider=self.name,
            tokens_used=tokens,
            cost_estimate=round(cost, 6),
        )
