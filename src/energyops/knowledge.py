"""Splitting documents into passages and turning text into vectors for search.

Search here is extractive: it finds the passages of an organisation's own documents that are
closest to a question and shows them as written, with the document they came from. Nothing is
summarised or paraphrased by a model, so nothing can be invented on the way.

Two embedders are provided. `OpenAICompatibleEmbedder` calls a provider's embeddings endpoint
and is the one to use for real meaning-based search. `HashingEmbedder` needs no provider and no
key: it hashes words and word pairs into a fixed-size vector, so it matches shared wording and
nothing more ("boiler" will not match "heating plant"). It is labelled lexical everywhere it
appears and is refused in the production environment.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from collections.abc import Sequence
from typing import Protocol

import httpx
from pydantic import SecretStr

from energyops.config import Environment

DIMENSIONS = 768
CHUNK_CHARS = 800
OVERLAP_CHARS = 100
MAX_CHUNKS = 400
MAX_DOCUMENT_BYTES = 1024 * 1024
EMBED_BATCH = 64

_WORD = re.compile(r"[a-z0-9]+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class EmbeddingError(RuntimeError):
    """The embedding provider failed or answered with something unusable."""


class DocumentRefused(ValueError):
    """The document cannot be stored. The message says why."""


class Embedder(Protocol):
    name: str
    semantic: bool  # False for a lexical embedder, which matches wording only
    dimensions: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


# ------------------------------------------------------------------ chunking


def decode_document(data: bytes) -> str:
    if len(data) > MAX_DOCUMENT_BYTES:
        raise DocumentRefused(f"The file is larger than {MAX_DOCUMENT_BYTES // 1024} KB.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DocumentRefused(
            "The file is not UTF-8 text. Upload plain text or markdown."
        ) from None
    if "\x00" in text:
        raise DocumentRefused("The file contains binary data. Upload plain text or markdown.")
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise DocumentRefused("The file is empty.")
    return text


def _split_long(paragraph: str, size: int) -> list[str]:
    """Break one over-long paragraph at sentence ends, then at spaces, then anywhere."""
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(paragraph):
        while len(sentence) > size:
            cut = sentence.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            head, sentence = sentence[:cut].strip(), sentence[cut:].strip()
            if current:
                pieces.append(current)
                current = ""
            pieces.append(head)
        if current and len(current) + 1 + len(sentence) > size:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


def chunk_text(text: str, *, size: int = CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> list[str]:
    """Passages of about `size` characters, split on paragraph breaks where possible, each
    starting with the tail of the one before so a sentence on a boundary is found either way."""
    if size <= 0 or not 0 <= overlap < size:
        raise ValueError("size must be positive and overlap smaller than size")
    paragraphs = [re.sub(r"[ \t]+", " ", p).strip() for p in re.split(r"\n\s*\n", text)]
    units: list[str] = []
    for paragraph in paragraphs:
        if not paragraph:
            continue
        units.extend(_split_long(paragraph, size) if len(paragraph) > size else [paragraph])

    chunks: list[str] = []
    current = ""
    for unit in units:
        if current and len(current) + 2 + len(unit) > size:
            chunks.append(current)
            tail = current[-overlap:].lstrip() if overlap else ""
            # Start the next passage on a word boundary, not in the middle of a word.
            if tail and current[-overlap - 1 : -overlap] not in ("", " ", "\n"):
                tail = tail.split(" ", 1)[1] if " " in tail else ""
            current = f"{tail}\n\n{unit}".strip() if tail else unit
        else:
            current = f"{current}\n\n{unit}" if current else unit
    if current:
        chunks.append(current)
    return chunks


def prepare_chunks(text: str) -> list[str]:
    chunks = chunk_text(text)
    if len(chunks) > MAX_CHUNKS:
        raise DocumentRefused(
            f"The document is too long: it makes {len(chunks)} passages and the limit is "
            f"{MAX_CHUNKS}. Split it into smaller files."
        )
    return chunks


# ------------------------------------------------------------------ embedders


def _features(text: str) -> Counter[str]:
    words = _WORD.findall(text.lower())
    counts: Counter[str] = Counter(words)
    counts.update(f"{a} {b}" for a, b in zip(words, words[1:], strict=False))
    return counts


class HashingEmbedder:
    """Feature hashing: each word and word pair adds to one of 768 slots, with a hashed sign so
    that collisions tend to cancel. Deterministic, offline, and lexical only."""

    name = "hashing-v1"
    semantic = False
    dimensions = DIMENSIONS

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> list[float]:
        vector = [0.0] * DIMENSIONS
        for feature, count in _features(text).items():
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            sign = 1.0 if value & 1 else -1.0
            vector[(value >> 1) % DIMENSIONS] += sign * (1.0 + math.log(count))
        norm = math.sqrt(sum(x * x for x in vector))
        return [x / norm for x in vector] if norm else vector


def has_content(text: str) -> bool:
    """Whether the text has any words an embedder could use."""
    return bool(_WORD.search(text.lower()))


class OpenAICompatibleEmbedder:
    """Calls `POST {base_url}/embeddings` as OpenAI's embeddings API documents it: `input` (a
    list of strings), `model`, and optionally `dimensions`; the answer's `data[].embedding` lists
    are matched back by `index`. The returned size is checked against the column size, because a
    vector of another length cannot be stored and silently truncating one would be wrong."""

    semantic = True
    dimensions = DIMENSIONS

    def __init__(
        self,
        *,
        base_url: str,
        api_key: SecretStr,
        model: str,
        send_dimensions: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.name = f"openai-compatible:{model}"
        self._url = base_url.rstrip("/") + "/embeddings"
        self._key = api_key
        self._model = model
        self._send_dimensions = send_dimensions
        self._client = httpx.AsyncClient(transport=transport, timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH):
            out.extend(await self._batch(list(texts[start : start + EMBED_BATCH])))
        return out

    async def _batch(self, texts: list[str]) -> list[list[float]]:
        body: dict[str, object] = {"input": texts, "model": self._model}
        if self._send_dimensions:
            body["dimensions"] = DIMENSIONS
        try:
            response = await self._client.post(
                self._url,
                json=body,
                headers={"Authorization": f"Bearer {self._key.get_secret_value()}"},
            )
        except httpx.HTTPError as exc:
            raise EmbeddingError(
                f"The embedding provider could not be reached ({type(exc).__name__})."
            ) from None
        if response.status_code != 200:
            # The body is not echoed: it may repeat the request, which holds document text.
            raise EmbeddingError(
                f"The embedding provider answered with status {response.status_code}."
            )
        try:
            rows = sorted(response.json()["data"], key=lambda row: row["index"])
            vectors = [[float(x) for x in row["embedding"]] for row in rows]
        except (KeyError, TypeError, ValueError):
            raise EmbeddingError(
                "The embedding provider's answer was not in the expected shape."
            ) from None
        if len(vectors) != len(texts):
            raise EmbeddingError(f"Asked for {len(texts)} embeddings and received {len(vectors)}.")
        for vector in vectors:
            if len(vector) != DIMENSIONS:
                raise EmbeddingError(
                    f"The provider returned {len(vector)} numbers per embedding but the store "
                    f"holds {DIMENSIONS}. Choose a model or setting that returns {DIMENSIONS}."
                )
            if not all(math.isfinite(x) for x in vector):
                raise EmbeddingError("The provider returned a number that is not finite.")
        return vectors


def make_embedder(
    *,
    environment: Environment,
    base_url: str | None,
    api_key: SecretStr | None,
    model: str | None,
    send_dimensions: bool = True,
) -> Embedder | None:
    """The embedder this deployment uses, or None when there is none to use (production without
    a configured provider). The lexical embedder is only ever a stand-in outside production."""
    if base_url and api_key and model:
        return OpenAICompatibleEmbedder(
            base_url=base_url, api_key=api_key, model=model, send_dimensions=send_dimensions
        )
    if environment is not Environment.PROD:
        return HashingEmbedder()
    return None
