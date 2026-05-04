"""Abstract LLM provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class LLMResponse:
    content: str
    model: str
    provider: str
    tokens_used: Optional[int] = None
    cost_estimate: Optional[float] = None


class LLMProvider(ABC):
    """Base class for all LLM providers."""

    @abstractmethod
    def analyze(self, prompt: str, system_prompt: str = "") -> LLMResponse:
        """Send a prompt and get a response."""

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this provider is configured and reachable."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier string."""


DEVPULSE_SYSTEM_PROMPT = """\
You are DevPulse, a personal developer productivity assistant. Analyze the developer's
activity data and provide actionable, specific insights. Be concise and direct.
Focus on:
1. Time allocation patterns (are they spending time where they intend to?)
2. Toil and automation opportunities
3. Context switching patterns and focus quality
4. Specific, actionable suggestions (not generic advice)
Do not be preachy or lecture about productivity. Be a helpful colleague who
noticed patterns they might have missed. Use plain language.\
"""
