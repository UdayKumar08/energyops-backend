import asyncio
import socket

import pytest

from energyops.models import (
    Message,
    ModelAuthError,
    ModelBadRequestError,
    ModelClient,
    ModelError,
    ModelProviderError,
    ModelRateLimitError,
    ModelRequest,
    ModelTimeoutError,
    StreamChunk,
    error_from_http_status,
)
from energyops.models.test_adapter import SimulatedProviderFailure, TestAdapter


def req(text: str = "hello there world", **kw) -> ModelRequest:
    return ModelRequest(messages=(Message("system", "be brief"), Message("user", text)), **kw)


async def collect(adapter: TestAdapter, request: ModelRequest) -> list[StreamChunk]:
    return [chunk async for chunk in adapter.stream(request)]


LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def host_of(address: object) -> object:
    return address[0] if isinstance(address, tuple) else address


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that reaches beyond this machine.

    Loopback is allowed because asyncio itself connects to 127.0.0.1 to build its event loop
    on Windows.
    """
    real_connect = socket.socket.connect
    real_getaddrinfo = socket.getaddrinfo

    def guarded_connect(self: socket.socket, address: object) -> None:
        if host_of(address) not in LOOPBACK:
            raise AssertionError(f"unexpected network connection to {address!r}")
        real_connect(self, address)  # type: ignore[arg-type]

    def guarded_getaddrinfo(host: object, *args: object, **kwargs: object):
        if host not in LOOPBACK:
            raise AssertionError(f"unexpected DNS lookup for {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)


def test_the_network_guard_blocks_outside_connections() -> None:
    with pytest.raises(AssertionError, match="unexpected"):
        socket.create_connection(("example.com", 80))
    with socket.socket() as sock, pytest.raises(AssertionError, match="unexpected"):
        sock.connect(("93.184.216.34", 80))


def test_adapter_satisfies_the_interface_and_is_labelled() -> None:
    client: ModelClient = TestAdapter()
    assert client.provider == "test-adapter"


def test_complete_is_deterministic_and_labelled() -> None:
    adapter = TestAdapter()
    first = asyncio.run(adapter.complete(req()))
    assert first == asyncio.run(adapter.complete(req()))
    assert first.text == "[test-adapter] echo: hello there world"
    assert first.provider == "test-adapter"


def test_scripted_reply_is_returned() -> None:
    assert asyncio.run(TestAdapter("fixed answer").complete(req())).text == "fixed answer"


def test_usage_is_reported_and_marked_estimated() -> None:
    response = asyncio.run(TestAdapter("one two three").complete(req()))
    assert response.usage.input_tokens == 2 + 3  # "be brief" + "hello there world"
    assert response.usage.output_tokens == 3
    assert response.usage.total_tokens == 8
    assert response.usage.estimated is True


def test_streaming_pieces_join_to_the_complete_text_and_end_with_usage() -> None:
    adapter = TestAdapter("alpha beta gamma")
    chunks = asyncio.run(collect(adapter, req()))
    assert len(chunks) > 2
    assert "".join(c.text for c in chunks) == "alpha beta gamma"
    assert all(c.finish_reason is None and c.usage is None for c in chunks[:-1])
    assert chunks[-1].finish_reason == "stop"
    assert chunks[-1].usage == asyncio.run(adapter.complete(req())).usage


def test_output_limit_truncates_and_reports_length() -> None:
    adapter = TestAdapter("a b c d e")
    response = asyncio.run(adapter.complete(req(max_output_tokens=2)))
    assert (response.text, response.finish_reason, response.usage.output_tokens) == (
        "a b",
        "length",
        2,
    )
    chunks = asyncio.run(collect(adapter, req(max_output_tokens=2)))
    assert "".join(c.text for c in chunks) == "a b" and chunks[-1].finish_reason == "length"


@pytest.mark.parametrize(
    ("status", "expected", "retryable"),
    [
        (401, ModelAuthError, False),
        (403, ModelAuthError, False),
        (400, ModelBadRequestError, False),
        (413, ModelBadRequestError, False),
        (429, ModelRateLimitError, True),
        (408, ModelTimeoutError, True),
        (504, ModelTimeoutError, True),
        (500, ModelProviderError, True),
        (503, ModelProviderError, True),
    ],
)
def test_provider_failures_map_to_our_errors(status: int, expected: type, retryable: bool) -> None:
    adapter = TestAdapter(fail_with=SimulatedProviderFailure(status, "provider said no"))
    with pytest.raises(expected) as caught:
        asyncio.run(adapter.complete(req()))
    assert caught.value.retryable is retryable
    assert caught.value.provider == "test-adapter"
    assert isinstance(caught.value, ModelError)
    with pytest.raises(expected):
        asyncio.run(collect(adapter, req()))


def test_a_stream_can_fail_part_way_through() -> None:
    adapter = TestAdapter(
        "a b c d", fail_with=SimulatedProviderFailure(503, "dropped"), fail_after_chunks=2
    )
    received: list[str] = []

    async def run() -> None:
        async for chunk in adapter.stream(req()):
            received.append(chunk.text)

    with pytest.raises(ModelProviderError):
        asyncio.run(run())
    assert received == ["a", " b"]


def test_error_mapping_helper_names_the_provider() -> None:
    assert error_from_http_status(429, "slow down", provider="x").provider == "x"


@pytest.mark.parametrize(
    "kwargs",
    [{"messages": ()}, {"max_output_tokens": 0}, {"temperature": 3.0}, {"timeout_seconds": 0}],
)
def test_invalid_requests_are_rejected(kwargs) -> None:
    args = {"messages": (Message("user", "hi")), **kwargs}
    with pytest.raises(ValueError):
        ModelRequest(**args)


def test_scripted_replies_play_in_order_and_the_last_repeats() -> None:
    adapter = TestAdapter(replies=["one", "two"])
    texts = [asyncio.run(adapter.complete(req())).text for _ in range(3)]
    assert texts == ["one", "two", "two"]
    with pytest.raises(ValueError, match="either"):
        TestAdapter("a", replies=["b"])
