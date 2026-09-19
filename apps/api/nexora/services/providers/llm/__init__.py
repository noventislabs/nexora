"""LLM providers. Importing this package registers every adapter."""

from nexora.config import settings
from nexora.core.errors import ProviderNotConfigured
from nexora.services.providers.llm import anthropic as _anthropic  # noqa: F401
from nexora.services.providers.llm import openai as _openai  # noqa: F401
from nexora.services.providers.llm.base import LLMMessage, LLMProvider, LLMResponse, llm_registry

__all__ = ["LLMMessage", "LLMProvider", "LLMResponse", "get_llm", "llm_registry"]


def get_llm() -> LLMProvider:
    """The configured LLM provider.

    Raises :class:`ProviderNotConfigured` when ``LLM_PROVIDER`` is unset, so callers
    surface NOT CONFIGURED instead of substituting generated-looking output.
    """
    name = (settings.llm_provider or "").strip().lower()
    if not name:
        raise ProviderNotConfigured(
            "LLM provider is NOT CONFIGURED. Set LLM_PROVIDER to 'anthropic' or 'openai' "
            "and supply the matching API key.",
            details={"missing_settings": ["LLM_PROVIDER"]},
        )
    provider = llm_registry.create(name)
    provider.require()
    return provider
