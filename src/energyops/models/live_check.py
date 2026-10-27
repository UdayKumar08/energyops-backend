"""Make one real call to a provider and print the result. Run it yourself, locally:

    uv run python -m energyops.models.live_check gemini|nebius [model-name]

It reads the key from your environment or local .env, prints only the model's answer, the
provider, the model and token usage, and never prints the key.
"""

from __future__ import annotations

import asyncio
import sys

from energyops.models.base import ModelClient
from energyops.models.credentials import ProviderKeys
from energyops.models.errors import ModelError
from energyops.models.providers import gemini_client, nebius_client
from energyops.models.types import Message, ModelRequest

PROMPT = "Reply with exactly the three words: energy check ok"


def build_client(name: str, keys: ProviderKeys, model: str | None = None) -> ModelClient:
    if name == "gemini":
        if keys.gemini_api_key is None:
            raise SystemExit("GEMINI_API_KEY is not set. Add it to your local .env file.")
        return (
            gemini_client(keys.gemini_api_key, model)
            if model
            else gemini_client(keys.gemini_api_key)
        )
    if name == "nebius":
        if keys.nebius_api_key is None:
            raise SystemExit("NEBIUS_API_KEY is not set. Add it to your local .env file.")
        return (
            nebius_client(keys.nebius_api_key, model)
            if model
            else nebius_client(keys.nebius_api_key)
        )
    raise SystemExit(f"Unknown provider {name!r}. Choose: gemini or nebius")


async def run(name: str, model: str | None = None) -> int:
    client = build_client(name, ProviderKeys(), model)
    request = ModelRequest(
        messages=(Message("user", PROMPT),), max_output_tokens=256, timeout_seconds=30
    )
    try:
        response = await client.complete(request)
    except ModelError as exc:
        print(f"FAILED ({type(exc).__name__}, retryable={exc.retryable}): {exc}")
        return 1
    print(f"provider: {response.provider}")
    print(f"model:    {response.model}")
    print(f"answer:   {response.text!r}")
    print(f"usage:    {response.usage}")
    print(f"finish:   {response.finish_reason}")
    return 0


def main() -> None:
    if len(sys.argv) not in (2, 3):
        raise SystemExit("Usage: python -m energyops.models.live_check gemini|nebius [model-name]")
    model = sys.argv[2] if len(sys.argv) == 3 else None
    raise SystemExit(asyncio.run(run(sys.argv[1], model)))


if __name__ == "__main__":
    main()
