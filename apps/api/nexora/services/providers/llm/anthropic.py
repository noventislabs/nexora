"""Anthropic Messages API adapter."""

from __future__ import annotations

from nexora.config import settings
from nexora.core.errors import ProviderUnavailable
from nexora.services.http import http_client, request_json
from nexora.services.providers.base import Availability
from nexora.services.providers.llm.base import LLMMessage, LLMProvider, LLMResponse, llm_registry

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


@llm_registry.register
class AnthropicProvider(LLMProvider):
    name = "anthropic"

    @property
    def model(self) -> str:
        return settings.anthropic_model

    def availability(self) -> Availability:
        if not settings.anthropic_api_key:
            return Availability.not_configured(
                provider=self.name,
                missing=("ANTHROPIC_API_KEY",),
                detail="Set ANTHROPIC_API_KEY to enable Anthropic-backed generation.",
            )
        return Availability.available(
            provider=self.name, detail=f"Anthropic {self.model}", model=self.model
        )

    def complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        max_tokens: int = 4096,
        temperature: float = 0.2,
        json_output: bool = False,
    ) -> LLMResponse:
        self.require()
        body: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": message.role, "content": message.content} for message in messages],
        }
        if json_output:
            # Prefilling the assistant turn is the documented way to force a bare JSON
            # object without a prose preamble.
            body["messages"] = [*body["messages"], {"role": "assistant", "content": "{"}]

        with http_client(
            timeout=120.0,
            headers={
                "x-api-key": settings.anthropic_api_key,
                "anthropic-version": API_VERSION,
                "content-type": "application/json",
            },
        ) as client:
            payload = request_json(client, "POST", API_URL, provider="Anthropic", json=body)

        blocks = payload.get("content") or []
        text = "".join(
            block.get("text", "") for block in blocks if isinstance(block, dict) and block.get("type") == "text"
        )
        if not text.strip():
            raise ProviderUnavailable(
                "Anthropic returned an empty completion.",
                details={"stop_reason": payload.get("stop_reason")},
            )
        if json_output:
            text = "{" + text

        usage = payload.get("usage") or {}
        return LLMResponse(
            text=text,
            provider=self.name,
            model=str(payload.get("model", self.model)),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            raw={"stop_reason": payload.get("stop_reason"), "id": payload.get("id")},
        )
