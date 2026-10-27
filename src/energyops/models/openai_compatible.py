"""One adapter for any provider that offers an OpenAI-style chat completions endpoint.

Google Gemini and Nebius both do, so each is a small configuration of this class (see
`energyops.models.providers`). The adapter uses the official `openai` package, turns its
exceptions into our own `ModelError` types, and never puts the API key into an error message.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import openai
from pydantic import SecretStr

from energyops.models.errors import (
    ModelError,
    ModelProviderError,
    ModelTimeoutError,
    error_from_http_status,
)
from energyops.models.types import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    StreamChunk,
    Usage,
)

MAX_ERROR_TEXT = 300


class OpenAICompatibleClient:
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        base_url: str,
        api_key: SecretStr,
        include_stream_usage: bool = False,
        sdk_client: openai.AsyncOpenAI | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self._key = api_key.get_secret_value()
        if not self._key:
            raise ValueError(f"{provider}: the API key is empty")
        self._include_stream_usage = include_stream_usage
        # We retry ourselves later, so the SDK must not retry silently.
        self._sdk = sdk_client or openai.AsyncOpenAI(
            api_key=self._key, base_url=base_url, max_retries=0
        )

    def __repr__(self) -> str:
        return f"OpenAICompatibleClient(provider={self.provider!r}, model={self.model!r})"

    # ------------------------------------------------------------------ errors

    def _redact(self, text: str) -> str:
        return text.replace(self._key, "[redacted]")[:MAX_ERROR_TEXT]

    def _map(self, exc: openai.OpenAIError) -> ModelError:
        if isinstance(exc, openai.APITimeoutError):
            return ModelTimeoutError(f"{self.provider} request timed out", provider=self.provider)
        if isinstance(exc, openai.APIConnectionError):
            return ModelProviderError(f"could not reach {self.provider}", provider=self.provider)
        if isinstance(exc, openai.APIStatusError):
            detail = self._redact(exc.message)
            return error_from_http_status(
                exc.status_code,
                f"{self.provider} returned HTTP {exc.status_code}: {detail}",
                provider=self.provider,
            )
        return ModelProviderError(f"{self.provider} call failed", provider=self.provider)

    # ------------------------------------------------------------------ requests

    def _params(self, request: ModelRequest) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "timeout": request.timeout_seconds,
        }

    @staticmethod
    def _finish(reason: str | None) -> FinishReason:
        return "length" if reason == "length" else "stop"

    @staticmethod
    def _usage(raw: Any, request: ModelRequest, output_text: str) -> Usage:
        if raw is not None and raw.prompt_tokens is not None and raw.completion_tokens is not None:
            return Usage(int(raw.prompt_tokens), int(raw.completion_tokens))
        # The provider did not report usage: fall back to a word count, clearly marked.
        words_in = sum(len(m.content.split()) for m in request.messages)
        return Usage(words_in, len(output_text.split()), estimated=True)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        try:
            response = await self._sdk.chat.completions.create(**self._params(request))
        except openai.OpenAIError as exc:
            raise self._map(exc) from None
        if not response.choices:
            raise ModelProviderError(f"{self.provider} returned no choices", provider=self.provider)
        choice = response.choices[0]
        text = choice.message.content or ""
        return ModelResponse(
            text=text,
            usage=self._usage(response.usage, request, text),
            finish_reason=self._finish(choice.finish_reason),
            provider=self.provider,
            model=self.model,
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        params = self._params(request)
        if self._include_stream_usage:
            params["stream_options"] = {"include_usage": True}
        finish: str | None = None
        raw_usage: Any = None
        pieces: list[str] = []
        try:
            events = await self._sdk.chat.completions.create(stream=True, **params)
            async for event in events:
                if event.usage is not None:
                    raw_usage = event.usage
                if not event.choices:
                    continue
                choice = event.choices[0]
                if choice.finish_reason:
                    finish = choice.finish_reason
                text = choice.delta.content
                if text:
                    pieces.append(text)
                    yield StreamChunk(text=text)
        except openai.OpenAIError as exc:
            raise self._map(exc) from None
        yield StreamChunk(
            text="",
            finish_reason=self._finish(finish),
            usage=self._usage(raw_usage, request, "".join(pieces)),
        )
