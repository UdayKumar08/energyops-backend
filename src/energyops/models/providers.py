"""Ready-made configurations for the providers we use. Values come from each provider's own
documentation; check them again before changing them."""

from __future__ import annotations

from pydantic import SecretStr

from energyops.models.openai_compatible import OpenAICompatibleClient

# https://ai.google.dev/gemini-api/docs/openai (Google marks this compatibility layer as beta)
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
# Chosen from a live check of which models a free-tier key could actually call: this alias
# answered in a few seconds. "latest" aliases can change which model they point to, so pin an
# exact model name before running evaluations. Models named in the docs but not callable by a
# key (for example gemini-3.8-flash, which returned HTTP 503) must not be assumed to work.
DEFAULT_GEMINI_MODEL = "gemini-flash-lite-latest"

# https://docs.tokenfactory.nebius.com/quickstart.md
NEBIUS_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"
# Picked from a live check of the models a real key could call: an exact, non-reasoning
# instruct model that answered in under a second. Reasoning models such as
# nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B returned empty content at a small token limit.
DEFAULT_NEBIUS_MODEL = "Qwen/Qwen3-30B-A3B-Instruct-2507"


def gemini_client(api_key: SecretStr, model: str = DEFAULT_GEMINI_MODEL) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        provider="google-gemini", model=model, base_url=GEMINI_BASE_URL, api_key=api_key
    )


def nebius_client(api_key: SecretStr, model: str = DEFAULT_NEBIUS_MODEL) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        provider="nebius", model=model, base_url=NEBIUS_BASE_URL, api_key=api_key
    )
