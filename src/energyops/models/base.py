"""The one interface the rest of the code uses to talk to a model."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from energyops.models.types import ModelRequest, ModelResponse, StreamChunk


class ModelClient(Protocol):
    """Implemented by every adapter (a real provider, or the test adapter).

    Adapters raise only `energyops.models.errors.ModelError` subclasses.
    """

    provider: str
    model: str

    async def complete(self, request: ModelRequest) -> ModelResponse: ...

    def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]: ...
