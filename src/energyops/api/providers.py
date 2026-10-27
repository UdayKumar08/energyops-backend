"""Which model a run may use, and how to build its client.

"test" is the labelled test adapter: no network, no key, and a canned correct answer, so a
screen can be built and tried without spending anything. Its runs are recorded with provider
"test-adapter" and can never be mistaken for a real model's output. The other providers need a
key in the local .env, and a missing key is reported, never guessed around.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from energyops.models.base import ModelClient
from energyops.models.credentials import ProviderKeys
from energyops.models.providers import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_NEBIUS_MODEL,
    gemini_client,
    nebius_client,
)
from energyops.models.test_adapter import MODEL_NAME, TestAdapter

ProviderId = Literal["test", "gemini", "nebius"]


class ProviderUnavailable(Exception):
    """The provider cannot be used right now, for example because its key is not set."""


class ProviderInfo(BaseModel):
    id: ProviderId
    label: str
    available: bool
    default_model: str
    note: str


def provider_infos(keys: ProviderKeys) -> list[ProviderInfo]:
    return [
        ProviderInfo(
            id="test",
            label="Test adapter (not a real model)",
            available=True,
            default_model=MODEL_NAME,
            note="No network. Returns a canned correct answer.",
        ),
        ProviderInfo(
            id="gemini",
            label="Google Gemini",
            available=keys.gemini_api_key is not None,
            default_model=DEFAULT_GEMINI_MODEL,
            note="" if keys.gemini_api_key else "GEMINI_API_KEY is not set in .env",
        ),
        ProviderInfo(
            id="nebius",
            label="Nebius",
            available=keys.nebius_api_key is not None,
            default_model=DEFAULT_NEBIUS_MODEL,
            note="" if keys.nebius_api_key else "NEBIUS_API_KEY is not set in .env",
        ),
    ]


def make_client(
    provider: ProviderId, model: str | None, keys: ProviderKeys, test_reply: str
) -> ModelClient:
    if provider == "test":
        return TestAdapter(test_reply)
    if provider == "gemini":
        if keys.gemini_api_key is None:
            raise ProviderUnavailable("GEMINI_API_KEY is not set in the local .env file.")
        return gemini_client(keys.gemini_api_key, model or DEFAULT_GEMINI_MODEL)
    if keys.nebius_api_key is None:
        raise ProviderUnavailable("NEBIUS_API_KEY is not set in the local .env file.")
    return nebius_client(keys.nebius_api_key, model or DEFAULT_NEBIUS_MODEL)
