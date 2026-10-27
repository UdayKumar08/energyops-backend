"""The adapter is tested against a fake HTTP transport, so no network and no real key is used."""

import asyncio
import json
from collections.abc import Callable

import httpx2
import openai
import pytest
from pydantic import SecretStr

from energyops.models import (
    Message,
    ModelAuthError,
    ModelBadRequestError,
    ModelProviderError,
    ModelRateLimitError,
    ModelRequest,
    ModelTimeoutError,
)
from energyops.models.credentials import ProviderKeys
from energyops.models.live_check import build_client
from energyops.models.openai_compatible import OpenAICompatibleClient
from energyops.models.providers import (
    DEFAULT_GEMINI_MODEL,
    GEMINI_BASE_URL,
    NEBIUS_BASE_URL,
    gemini_client,
)

KEY = "sk-test-NOT-A-REAL-KEY-123"
BASE = "https://example.invalid/v1/"


def make_client(
    handler: Callable[[httpx2.Request], httpx2.Response], **kw
) -> OpenAICompatibleClient:
    sdk = openai.AsyncOpenAI(
        api_key=KEY,
        base_url=BASE,
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    return OpenAICompatibleClient(
        provider="fake", model="m1", base_url=BASE, api_key=SecretStr(KEY), sdk_client=sdk, **kw
    )


def req(**kw) -> ModelRequest:
    return ModelRequest(messages=(Message("system", "be brief"), Message("user", "hi")), **kw)


def completion(text: str | None = "hello", finish: str = "stop", usage: bool = True) -> dict:
    body = {
        "id": "x",
        "object": "chat.completion",
        "created": 0,
        "model": "m1",
        "choices": [
            {"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": text}}
        ],
    }
    if usage:
        body["usage"] = {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
    return body


def sse(events: list[dict | str]) -> httpx2.Response:
    lines = [f"data: {e if isinstance(e, str) else json.dumps(e)}\n\n" for e in events]
    return httpx2.Response(
        200, headers={"content-type": "text/event-stream"}, content="".join(lines).encode()
    )


def delta(text: str | None, finish: str | None = None) -> dict:
    return {
        "id": "x",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "m1",
        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": finish}],
    }


# ------------------------------------------------------------------ complete


def test_complete_sends_the_expected_request_and_parses_the_answer() -> None:
    seen: dict = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["url"] = str(request.url)
        seen["auth_ok"] = request.headers["authorization"] == f"Bearer {KEY}"
        seen["body"] = json.loads(request.content)
        return httpx2.Response(200, json=completion("hello world"))

    response = asyncio.run(
        make_client(handler).complete(req(max_output_tokens=50, temperature=0.2))
    )
    assert seen["url"] == BASE + "chat/completions"
    assert seen["auth_ok"] is True
    assert seen["body"]["model"] == "m1"
    assert seen["body"]["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
    ]
    assert seen["body"]["max_tokens"] == 50 and seen["body"]["temperature"] == 0.2
    assert "stream" not in seen["body"] or seen["body"]["stream"] is False
    assert (response.text, response.finish_reason) == ("hello world", "stop")
    assert (response.provider, response.model) == ("fake", "m1")
    assert (response.usage.input_tokens, response.usage.output_tokens) == (7, 3)
    assert response.usage.estimated is False


def test_missing_usage_falls_back_to_a_marked_estimate() -> None:
    client = make_client(lambda r: httpx2.Response(200, json=completion("a b c", usage=False)))
    usage = asyncio.run(client.complete(req())).usage
    assert usage.estimated is True and usage.output_tokens == 3 and usage.input_tokens == 3


def test_length_finish_reason_and_null_content() -> None:
    client = make_client(lambda r: httpx2.Response(200, json=completion(None, finish="length")))
    response = asyncio.run(client.complete(req()))
    assert (response.text, response.finish_reason) == ("", "length")


def test_no_choices_is_a_provider_error() -> None:
    body = {**completion(), "choices": []}
    client = make_client(lambda r: httpx2.Response(200, json=body))
    with pytest.raises(ModelProviderError):
        asyncio.run(client.complete(req()))


# ------------------------------------------------------------------ streaming


def test_streaming_yields_text_then_a_final_chunk() -> None:
    events = [delta("Hel"), delta("lo"), delta(None, "stop"), "[DONE]"]
    client = make_client(lambda r: sse(events))

    async def collect():
        return [c async for c in client.stream(req())]

    chunks = asyncio.run(collect())
    assert "".join(c.text for c in chunks) == "Hello"
    assert chunks[-1].finish_reason == "stop"
    assert chunks[-1].usage is not None and chunks[-1].usage.estimated is True


def test_stream_usage_is_requested_only_when_enabled_and_used_when_reported() -> None:
    bodies: list[dict] = []
    final = {**delta(None), "choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 2}}

    def silent(request: httpx2.Request) -> httpx2.Response:  # provider reports no usage
        bodies.append(json.loads(request.content))
        return sse([delta("ok", "stop"), "[DONE]"])

    def reporting(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        return sse([delta("ok", "stop"), final, "[DONE]"])

    async def last(client: OpenAICompatibleClient):
        return [c async for c in client.stream(req())][-1]

    off = asyncio.run(last(make_client(silent)))
    on = asyncio.run(last(make_client(reporting, include_stream_usage=True)))
    assert "stream_options" not in bodies[0]
    assert bodies[1]["stream_options"] == {"include_usage": True}
    assert off.usage is not None and off.usage.estimated is True
    assert on.usage is not None and (on.usage.input_tokens, on.usage.output_tokens) == (4, 2)
    assert on.usage.estimated is False


def test_http_error_when_a_stream_starts_is_mapped() -> None:
    client = make_client(lambda r: httpx2.Response(429, json={"error": {"message": "slow down"}}))

    async def collect():
        return [c async for c in client.stream(req())]

    with pytest.raises(ModelRateLimitError):
        asyncio.run(collect())


# ------------------------------------------------------------------ errors


@pytest.mark.parametrize(
    ("status", "expected", "retryable"),
    [
        (401, ModelAuthError, False),
        (403, ModelAuthError, False),
        (400, ModelBadRequestError, False),
        (404, ModelBadRequestError, False),
        (429, ModelRateLimitError, True),
        (504, ModelTimeoutError, True),
        (500, ModelProviderError, True),
        (503, ModelProviderError, True),
    ],
)
def test_http_errors_map_to_our_errors(status: int, expected: type, retryable: bool) -> None:
    client = make_client(
        lambda r: httpx2.Response(status, json={"error": {"message": "provider said no"}})
    )
    with pytest.raises(expected) as caught:
        asyncio.run(client.complete(req()))
    assert caught.value.retryable is retryable
    assert caught.value.provider == "fake"
    assert "provider said no" in str(caught.value)


def test_timeout_and_connection_failures_map_to_our_errors() -> None:
    def timeout(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectTimeout("slow", request=request)

    def refused(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    with pytest.raises(ModelTimeoutError):
        asyncio.run(make_client(timeout).complete(req()))
    with pytest.raises(ModelProviderError) as caught:
        asyncio.run(make_client(refused).complete(req()))
    assert caught.value.retryable is True


def test_the_key_never_appears_in_errors_repr_or_chained_exceptions() -> None:
    client = make_client(
        lambda r: httpx2.Response(401, json={"error": {"message": f"bad key {KEY} given"}})
    )
    with pytest.raises(ModelAuthError) as caught:
        asyncio.run(client.complete(req()))
    assert KEY not in str(caught.value)
    assert "[redacted]" in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    assert KEY not in repr(client) and KEY not in str(client)


def test_an_empty_key_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="empty"):
        OpenAICompatibleClient(provider="fake", model="m", base_url=BASE, api_key=SecretStr(""))


# ------------------------------------------------------------------ provider config


def test_gemini_configuration_matches_the_documented_endpoint() -> None:
    client = gemini_client(SecretStr(KEY))
    assert (client.provider, client.model) == ("google-gemini", DEFAULT_GEMINI_MODEL)
    assert GEMINI_BASE_URL == "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert NEBIUS_BASE_URL == "https://api.tokenfactory.nebius.com/v1/"


def test_provider_keys_load_from_env_and_stay_hidden(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here, so only the environment is read
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    keys = ProviderKeys()
    assert keys.gemini_api_key is not None and keys.gemini_api_key.get_secret_value() == KEY
    assert keys.nebius_api_key is None
    assert KEY not in repr(keys) and KEY not in str(keys)


def test_live_check_explains_a_missing_key_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="GEMINI_API_KEY is not set"):
        build_client("gemini", ProviderKeys())


def test_live_check_can_override_the_model(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    assert build_client("gemini", ProviderKeys()).model == DEFAULT_GEMINI_MODEL
    assert build_client("gemini", ProviderKeys(), "some-other-model").model == "some-other-model"
