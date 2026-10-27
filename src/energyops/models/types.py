"""Plain data types shared by every model adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Role = Literal["system", "user", "assistant"]
FinishReason = Literal["stop", "length"]


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class Usage:
    """Token counts. `estimated` is True when they were not reported by a real provider."""

    input_tokens: int
    output_tokens: int
    estimated: bool = False

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class ModelRequest:
    messages: tuple[Message, ...]
    max_output_tokens: int = 1024
    temperature: float = 0.0
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.messages:
            raise ValueError("messages must not be empty")
        if self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be at least 1")
        if not 0 <= self.temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


@dataclass(frozen=True, slots=True)
class ModelResponse:
    text: str
    usage: Usage
    finish_reason: FinishReason
    provider: str
    model: str


@dataclass(frozen=True, slots=True)
class StreamChunk:
    """One piece of a streamed answer. The last chunk has `finish_reason` and `usage` set."""

    text: str
    finish_reason: FinishReason | None = None
    usage: Usage | None = None
    extra: dict[str, str] = field(default_factory=dict)
