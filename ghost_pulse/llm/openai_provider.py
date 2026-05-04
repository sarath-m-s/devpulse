"""OpenAI (and compatible) LLM provider."""

from __future__ import annotations

import os
from typing import Any

from ghost_pulse.llm.base import LLMProvider, LLMResponse


class OpenAIProvider(LLMProvider):
    """Uses the OpenAI SDK; also works with any OpenAI-compatible API."""

    DEFAULT_MODEL = "gpt-4o-mini"

    def __init__(self, api_key: str = "", model: str = "", base_url: str = "") -> None:
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._model = model or self.DEFAULT_MODEL
        self._base_url = base_url or None
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from openai import OpenAI
                kwargs: dict[str, Any] = {"api_key": self._api_key}
                if self._base_url:
                    kwargs["base_url"] = self._base_url
                self._client = OpenAI(**kwargs)
            except ImportError as exc:
                raise RuntimeError(
                    "openai package not installed. Run: pip install 'ghost-pulse[openai]'"
                ) from exc
        return self._client

    @property
    def name(self) -> str:
        return "openai"

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
        # gpt-4o-mini: $0.15/$0.60 per MTok
        cost = None
        if response.usage:
            cost = round(
                response.usage.prompt_tokens * 0.15e-6
                + response.usage.completion_tokens * 0.60e-6,
                6,
            )
        return LLMResponse(
            content=content,
            model=self._model,
            provider=self.name,
            tokens_used=tokens,
            cost_estimate=cost,
        )
