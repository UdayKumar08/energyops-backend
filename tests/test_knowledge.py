"""Chunking, the embedders, and knowledge search through the API on a real PostgreSQL."""

from __future__ import annotations

import json
import math

import httpx
import pytest
from pydantic import SecretStr

from energyops.config import Environment
from energyops.knowledge import (
    DIMENSIONS,
    DocumentRefused,
    EmbeddingError,
    HashingEmbedder,
    OpenAICompatibleEmbedder,
    chunk_text,
    decode_document,
    make_embedder,
    prepare_chunks,
)
from service_helpers import ADMIN, MANAGER, NORTH, PLATFORM, TECH, VIEWER, as_user

NORTH_MANAGER = "manager@northside.example"
BOILER = (
    "Boiler lockout procedure\n\nIf the boiler locks out, wait five minutes, then press the "
    "reset button once. Do not press reset a second time. Call the heating contractor if the "
    "lockout repeats.\n\nChiller start-up\n\nOpen the isolation valves before starting the "
    "chiller and check the water pressure."
)
CLINIC = (
    "Oxygen plant checks\n\nEvery morning the porter logs the oxygen plant pressure and reports "
    "any reading below the marked line to the duty engineer."
)


# ------------------------------------------------------------------ chunking and text


def test_short_text_is_one_passage_and_long_text_is_split_with_overlap() -> None:
    assert chunk_text("A short note.") == ["A short note."]
    text = "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(12))
    chunks = chunk_text(text, size=400, overlap=60)
    assert len(chunks) > 3 and all(len(c) <= 400 + 60 + 2 for c in chunks)
    # Each passage starts with the end of the one before it.
    for earlier, later in zip(chunks, chunks[1:], strict=False):
        assert later.split("\n\n")[0].split(" ")[0] in earlier


def test_one_enormous_paragraph_is_still_split() -> None:
    chunks = chunk_text("This is a sentence. " * 500, size=300, overlap=0)
    assert len(chunks) > 20 and all(len(c) <= 300 for c in chunks)
    chunks = chunk_text("x" * 2000, size=300, overlap=0)
    assert all(len(c) <= 300 for c in chunks) and "".join(chunks) == "x" * 2000


def test_bad_chunk_settings_are_refused() -> None:
    with pytest.raises(ValueError):
        chunk_text("x", size=0)
    with pytest.raises(ValueError):
        chunk_text("x", size=10, overlap=10)


def test_documents_that_cannot_be_stored_are_refused() -> None:
    with pytest.raises(DocumentRefused, match="UTF-8"):
        decode_document(b"\xff\xfe\xfa")
    with pytest.raises(DocumentRefused, match="binary"):
        decode_document(b"abc\x00def")
    with pytest.raises(DocumentRefused, match="empty"):
        decode_document(b" \n\n ")
    with pytest.raises(DocumentRefused, match="larger"):
        decode_document(b"a" * (1024 * 1024 + 1))
    with pytest.raises(DocumentRefused, match="too long"):
        prepare_chunks("\n\n".join("word " * 150 for _ in range(500)))


# ------------------------------------------------------------------ embedders


async def test_the_hashing_embedder_is_unit_length_and_repeatable() -> None:
    embedder = HashingEmbedder()
    (a, b, c) = await embedder.embed(
        ["reset the boiler", "reset the boiler", "chiller water pressure"]
    )
    assert len(a) == DIMENSIONS and a == b
    assert math.isclose(sum(x * x for x in a), 1.0, rel_tol=1e-9)
    close = sum(x * y for x, y in zip(a, b, strict=True))
    far = sum(x * y for x, y in zip(a, c, strict=True))
    assert close > 0.99 and far < 0.2
    assert embedder.semantic is False


def test_production_without_a_provider_has_no_embedder() -> None:
    assert (
        make_embedder(environment=Environment.PROD, base_url=None, api_key=None, model=None) is None
    )
    local = make_embedder(environment=Environment.LOCAL, base_url=None, api_key=None, model=None)
    assert isinstance(local, HashingEmbedder)
    real = make_embedder(
        environment=Environment.PROD,
        base_url="https://example.invalid/v1",
        api_key=SecretStr("k"),
        model="m",
    )
    assert isinstance(real, OpenAICompatibleEmbedder) and real.semantic


def provider(handler: httpx.MockTransport) -> OpenAICompatibleEmbedder:
    return OpenAICompatibleEmbedder(
        base_url="https://embeddings.example/v1/",
        api_key=SecretStr("secret-key"),
        model="embed-1",
        transport=handler,
    )


async def test_the_provider_request_follows_the_documented_shape() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        inputs = json.loads(request.content)["input"]
        # Answer out of order, to show the index is what matches vectors to inputs.
        data = [{"index": i, "embedding": [float(i)] * DIMENSIONS} for i in range(len(inputs))]
        return httpx.Response(200, json={"data": list(reversed(data))})

    texts = [f"text {i}" for i in range(130)]
    vectors = await provider(httpx.MockTransport(handle)).embed(texts)
    assert len(seen) == 3  # batches of 64, 64 and 2
    first = json.loads(seen[0].content)
    assert first["model"] == "embed-1" and first["dimensions"] == DIMENSIONS
    assert str(seen[0].url) == "https://embeddings.example/v1/embeddings"
    assert seen[0].headers["authorization"] == "Bearer secret-key"
    assert [v[0] for v in vectors[:3]] == [0.0, 1.0, 2.0] and len(vectors) == 130


async def test_dimensions_can_be_left_out_for_a_provider_that_rejects_them() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert "dimensions" not in json.loads(request.content)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1] * DIMENSIONS}]})

    embedder = OpenAICompatibleEmbedder(
        base_url="https://e.example",
        api_key=SecretStr("k"),
        model="m",
        send_dimensions=False,
        transport=httpx.MockTransport(handle),
    )
    assert len(await embedder.embed(["x"])) == 1


@pytest.mark.parametrize(
    "response, message",
    [
        (httpx.Response(401, text="bad key secret-key and the text sent"), "status 401"),
        (httpx.Response(200, json={"oops": 1}), "expected shape"),
        (httpx.Response(200, text="not json"), "expected shape"),
        (httpx.Response(200, json={"data": []}), "received 0"),
        (httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1] * 3072}]}), "3072"),
        (
            httpx.Response(
                200,
                content=b'{"data": [{"index": 0, "embedding": [NaN' + b", 0.0" * 767 + b"]}]}",
            ),
            "finite",
        ),
    ],
)
async def test_a_bad_provider_answer_is_an_error_that_leaks_nothing(
    response: httpx.Response, message: str
) -> None:
    embedder = provider(httpx.MockTransport(lambda request: response))
    with pytest.raises(EmbeddingError, match=message) as caught:
        await embedder.embed(["secret document text"])
    assert "secret-key" not in str(caught.value) and "secret document" not in str(caught.value)


async def test_an_unreachable_provider_is_an_error() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    with pytest.raises(EmbeddingError, match="could not be reached"):
        await provider(httpx.MockTransport(refuse)).embed(["x"])


# ------------------------------------------------------------------ the API


async def add(
    api: httpx.AsyncClient, user: str, text: str = BOILER, title: str = "Plant notes"
) -> httpx.Response:
    return await api.post(
        "/knowledge/documents",
        content=text.encode(),
        params={"title": title, "filename": "notes.md"},
        headers={**as_user(user), "Content-Type": "text/markdown"},
    )


async def find(
    api: httpx.AsyncClient, user: str, question: str, **params: object
) -> httpx.Response:
    return await api.get(
        "/knowledge/search", params={"q": question, **params}, headers=as_user(user)
    )


async def test_a_manager_adds_a_document_and_anyone_in_the_organisation_can_search_it(
    api: httpx.AsyncClient,
) -> None:
    created = await add(api, MANAGER)
    assert created.status_code == 201, created.text
    assert created.json()["passages"] >= 1 and created.json()["searchable"]
    for user in (VIEWER, TECH, ADMIN):
        found = await find(api, user, "what do I do when the boiler locks out")
        assert found.status_code == 200
        body = found.json()
        assert body["semantic"] is False and "wording" in body["note"]
        top = body["results"][0]
        assert top["title"] == "Plant notes" and "reset button" in top["text"]
        assert 0.05 <= top["score"] <= 1.0
    # Results are the document's own words, unchanged.
    assert top["text"] in BOILER.replace("\n\n", "\n\n")


async def test_unrelated_questions_find_nothing_rather_than_something_irrelevant(
    api: httpx.AsyncClient,
) -> None:
    await add(api, MANAGER)
    body = (await find(api, MANAGER, "quarterly staff holiday rota")).json()
    assert body["results"] == []
    assert (await find(api, MANAGER, "?!?")).json()["results"] == []


async def test_search_never_crosses_organisations(api: httpx.AsyncClient) -> None:
    harbour = (await add(api, MANAGER)).json()
    assert (await add(api, NORTH_MANAGER, CLINIC, "Clinic notes")).status_code == 201

    north_boiler = (await find(api, NORTH_MANAGER, "boiler lockout reset button")).json()
    assert north_boiler["results"] == []
    harbour_oxygen = (await find(api, MANAGER, "oxygen plant pressure")).json()
    assert harbour_oxygen["results"] == []
    own = (await find(api, NORTH_MANAGER, "oxygen plant pressure")).json()
    assert [r["title"] for r in own["results"]] == ["Clinic notes"]

    assert [
        d["title"] for d in (await api.get("/knowledge/documents", headers=as_user(NORTH))).json()
    ] == ["Clinic notes"]
    # Deleting another organisation's document looks like deleting one that is not there, and
    # it leaves the document alone.
    gone = await api.delete(f"/knowledge/documents/{harbour['id']}", headers=as_user(NORTH_MANAGER))
    missing = await api.delete(
        "/knowledge/documents/00000000-0000-0000-0000-000000000000", headers=as_user(NORTH_MANAGER)
    )
    assert gone.status_code == missing.status_code == 404 and gone.json() == missing.json()
    assert (await find(api, MANAGER, "boiler lockout")).json()["results"]


async def test_only_managers_change_the_library_and_nobody_without_an_organisation_reads_it(
    api: httpx.AsyncClient,
) -> None:
    for user in (VIEWER, TECH, PLATFORM):
        assert (await add(api, user)).status_code == 403
    for user in (PLATFORM,):
        assert (await find(api, user, "boiler")).status_code == 403
    created = (await add(api, MANAGER)).json()
    assert (
        await api.delete(f"/knowledge/documents/{created['id']}", headers=as_user(VIEWER))
    ).status_code == 403


async def test_deleting_a_document_removes_its_passages_and_is_audited(
    api: httpx.AsyncClient,
) -> None:
    created = (await add(api, MANAGER)).json()
    deleted = await api.delete(f"/knowledge/documents/{created['id']}", headers=as_user(MANAGER))
    assert deleted.status_code == 204
    assert (await find(api, MANAGER, "boiler lockout")).json()["results"] == []
    assert (await api.get("/knowledge/documents", headers=as_user(MANAGER))).json() == []
    actions = [a["action"] for a in (await api.get("/audit", headers=as_user(ADMIN))).json()]
    assert "knowledge.upload" in actions and "knowledge.delete" in actions


async def test_bad_documents_are_refused_with_a_reason(api: httpx.AsyncClient) -> None:
    assert (await add(api, MANAGER, "  \n ")).status_code == 422
    assert (await add(api, MANAGER, "!!! ??? ...")).status_code == 422
    binary = await api.post(
        "/knowledge/documents",
        content=b"abc\x00\x01",
        params={"title": "x"},
        headers=as_user(MANAGER),
    )
    assert binary.status_code == 422
    huge = await api.post(
        "/knowledge/documents",
        content=b"a " * (600 * 1024),
        params={"title": "huge"},
        headers=as_user(MANAGER),
    )
    assert huge.status_code == 413
    no_title = await api.post(
        "/knowledge/documents", content=b"hello world", headers=as_user(MANAGER)
    )
    assert no_title.status_code == 422
    assert (await api.get("/knowledge/documents", headers=as_user(MANAGER))).json() == []


async def test_without_an_embedder_knowledge_search_is_off(api: httpx.AsyncClient) -> None:
    api._transport.app.state.service.embedder = None  # type: ignore[attr-defined]
    assert (await add(api, MANAGER)).status_code == 503
    assert (await find(api, MANAGER, "boiler")).status_code == 503


async def test_documents_made_by_another_embedder_are_not_searched_but_are_counted(
    api: httpx.AsyncClient,
) -> None:
    await add(api, MANAGER)
    state = api._transport.app.state.service  # type: ignore[attr-defined]

    class Other(HashingEmbedder):
        name = "other-v2"

    state.embedder = Other()
    body = (await find(api, MANAGER, "boiler lockout")).json()
    assert body["results"] == [] and body["unsearchable_documents"] == 1
    listed = (await api.get("/knowledge/documents", headers=as_user(MANAGER))).json()
    assert listed[0]["searchable"] is False


async def test_a_provider_failure_is_a_502_that_stores_nothing(api: httpx.AsyncClient) -> None:
    state = api._transport.app.state.service  # type: ignore[attr-defined]

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    state.embedder = provider(httpx.MockTransport(refuse))
    assert (await add(api, MANAGER)).status_code == 502
    state.embedder = HashingEmbedder()
    assert (await api.get("/knowledge/documents", headers=as_user(MANAGER))).json() == []


async def test_the_assistant_uses_both_capabilities_for_the_caller(
    api: httpx.AsyncClient,
) -> None:
    await add(api, MANAGER)
    forecast = await api.post(
        "/assistant/ask",
        json={"question": "What will demand be next week?", "site": "demo-hotel"},
        headers=as_user(MANAGER),
    )
    out = forecast.json()["run"]["output"]
    assert out["route"] == "forecast" and out["outcome"] == "answered"
    assert len(out["result"]["days"]) == 7 and "points" not in out["result"]

    asked = await api.post(
        "/assistant/ask",
        json={"question": "How do I reset the boiler after a lockout? Is there a procedure?"},
        headers=as_user(MANAGER),
    )
    out = asked.json()["run"]["output"]
    assert out["route"] == "knowledge" and out["outcome"] == "answered"
    assert "reset button" in out["result"]["results"][0]["text"]

    other = await api.post(
        "/assistant/ask",
        json={"question": "How do I reset the boiler after a lockout? Is there a procedure?"},
        headers=as_user(NORTH_MANAGER),
    )
    assert other.json()["run"]["output"]["result"]["results"] == []
