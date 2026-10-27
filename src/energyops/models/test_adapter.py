"""A deterministic stand-in for a model. THIS IS A TEST ADAPTER, NOT A REAL MODEL.

It makes no network calls and has no intelligence. It either returns a scripted reply or a fixed
echo of the last user message, and it can be told to fail so error handling can be tested. Its
token counts are word counts and are always marked `estimated=True`. Any answer that came from it
carries provider "test-adapter" so it can never be mistaken for real model output.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from energyops.models.errors import ModelError, error_from_http_status
from energyops.models.types import ModelRequest, ModelResponse, StreamChunk, Usage

PROVIDER_NAME = "test-adapter"
MODEL_NAME = "deterministic-v1"


@dataclass(frozen=True, slots=True)
class SimulatedProviderFailure(Exception):
    """What a real provider SDK might raise: an HTTP status and a message."""

    status: int
    message: str


class TestAdapter:
    __test__ = False  # stop pytest treating this class as a test case

    provider = PROVIDER_NAME
    model = MODEL_NAME

    def __init__(
        self,
        reply: str | None = None,
        *,
        fail_with: SimulatedProviderFailure | None = None,
        fail_after_chunks: int | None = None,
    ) -> None:
        self._reply = reply
        self._fail_with = fail_with
        self._fail_after_chunks = fail_after_chunks

    def _map(self, failure: SimulatedProviderFailure) -> ModelError:
        return error_from_http_status(failure.status, failure.message, provider=self.provider)

    def _text_for(self, request: ModelRequest) -> str:
        if self._reply is not None:
            return self._reply
        last_user = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
        return f"[test-adapter] echo: {last_user}"

    @staticmethod
    def _words(text: str) -> list[str]:
        return text.split()

    def _usage(self, request: ModelRequest, output_words: int) -> Usage:
        input_words = sum(len(self._words(m.content)) for m in request.messages)
        return Usage(input_tokens=input_words, output_tokens=output_words, estimated=True)

    def _truncate(self, request: ModelRequest, words: list[str]) -> tuple[list[str], bool]:
        cut = len(words) > request.max_output_tokens
        return (words[: request.max_output_tokens] if cut else words), cut

    async def complete(self, request: ModelRequest) -> ModelResponse:
        if self._fail_with is not None:
            raise self._map(self._fail_with)
        words, cut = self._truncate(request, self._words(self._text_for(request)))
        return ModelResponse(
            text=" ".join(words),
            usage=self._usage(request, len(words)),
            finish_reason="length" if cut else "stop",
            provider=self.provider,
            model=self.model,
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        if self._fail_with is not None and self._fail_after_chunks is None:
            raise self._map(self._fail_with)
        words, cut = self._truncate(request, self._words(self._text_for(request)))
        for index, word in enumerate(words):
            if self._fail_with is not None and index == self._fail_after_chunks:
                raise self._map(self._fail_with)
            yield StreamChunk(text=word if index == 0 else f" {word}")
        yield StreamChunk(
            text="",
            finish_reason="length" if cut else "stop",
            usage=self._usage(request, len(words)),
        )
