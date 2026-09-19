"""LLMProvider interface.

Used for topic generation, research synthesis, script writing, fact-check reasoning and
metadata drafting. There is no offline fallback that invents content: when no provider
is configured, callers receive ``NOT_CONFIGURED`` and the pipeline stops.
"""

from __future__ import annotations

import abc
import json
import re
from dataclasses import dataclass, field
from typing import Any

from nexora.core.errors import ProviderUnavailable
from nexora.services.providers.base import Provider, ProviderRegistry


@dataclass(frozen=True)
class LLMMessage:
    role: str  # "user" | "assistant"
    content: str


@dataclass(frozen=True)
class LLMResponse:
    text: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def json(self) -> Any:
        """Parse the response as JSON, tolerating a fenced code block wrapper.

        Raises :class:`ProviderUnavailable` rather than returning a guess, so a
        malformed model reply can never be mistaken for real content.
        """
        text = self.text.strip()
        fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            start, end = text.find("{"), text.rfind("}")
            if start != -1 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    pass
            start, end = text.find("["), text.rfind("]")
            if start != -1 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    pass
            raise ProviderUnavailable(
                f"{self.provider} returned a response that is not valid JSON.",
                details={"model": self.model, "excerpt": self.text[:400]},
            ) from exc


class LLMProvider(Provider, abc.ABC):
    kind = "llm"

    @property
    @abc.abstractmethod
    def model(self) -> str: ...

    @abc.abstractmethod
    def complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        max_tokens: int = 4096,
        temperature: float = 0.2,
        json_output: bool = False,
    ) -> LLMResponse:
        """Run one completion. Implementations must not retry silently forever."""


llm_registry: ProviderRegistry[LLMProvider] = ProviderRegistry("llm")
