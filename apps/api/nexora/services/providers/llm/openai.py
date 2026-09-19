"""OpenAI Chat Completions adapter."""

from __future__ import annotations

from nexora.config import settings
from nexora.core.errors import ProviderUnavailable
from nexora.services.http import http_client, request_json
from nexora.services.providers.base import Availability
from nexora.services.providers.llm.base import LLMMessage, LLMProvider, LLMResponse, llm_registry

API_URL = "https://api.openai.com/v1/chat/completions"


@llm_registry.register
class OpenAIProvider(LLMProvider):
    name = "openai"

    @property
    def model(self) -> str:
        return settings.openai_model

    def availability(self) -> Availability:
        if not settings.openai_api_key:
            return Availability.not_configured(
                provider=self.name,
                missing=("OPENAI_API_KEY",),
                detail="Set OPENAI_API_KEY to enable OpenAI-backed generation.",
            )
        return Availability.available(
            provider=self.name, detail=f"OpenAI {self.model}", model=self.model
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
            "max_completion_tokens": max_tokens,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                *({"role": message.role, "content": message.content} for message in messages),
            ],
        }
        if json_output:
            body["response_format"] = {"type": "json_object"}

        with http_client(
            timeout=120.0,
            headers={
                "authorization": f"Bearer {settings.openai_api_key}",
                "content-type": "application/json",
            },
        ) as client:
            payload = request_json(client, "POST", API_URL, provider="OpenAI", json=body)

        choices = payload.get("choices") or []
        text = ""
        if choices and isinstance(choices[0], dict):
            text = (choices[0].get("message") or {}).get("content") or ""
        if not text.strip():
            raise ProviderUnavailable(
                "OpenAI returned an empty completion.",
                details={"finish_reason": choices[0].get("finish_reason") if choices else None},
            )

        usage = payload.get("usage") or {}
        return LLMResponse(
            text=text,
            provider=self.name,
            model=str(payload.get("model", self.model)),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            raw={"id": payload.get("id")},
        )
