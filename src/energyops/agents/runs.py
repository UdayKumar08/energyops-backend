"""What one agent run leaves behind: the outcome, the token usage and a step by step trace.

This is the data behind a "Runs" tab and a "View trace" button. It is plain data that can be
turned into JSON, and it never contains an API key.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class RunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class TraceStep(BaseModel):
    name: str
    started_at: datetime
    duration_ms: float
    data: dict[str, Any] = Field(default_factory=dict)


class UsageTotals(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    estimated: bool = False


class RunRecord(BaseModel):
    run_id: str
    agent: str
    prompt_version: str
    prompt_sha256: str
    model_provider: str
    model_name: str
    status: RunStatus
    started_at: datetime
    finished_at: datetime
    input: dict[str, Any]
    output: dict[str, Any] | None = None
    error_kind: str | None = None
    error: str | None = None
    attempts: int = 0
    usage: UsageTotals = Field(default_factory=UsageTotals)
    steps: list[TraceStep] = Field(default_factory=list)


class TraceRecorder:
    """Collects trace steps while a run is in progress."""

    def __init__(self) -> None:
        self.run_id = str(uuid4())
        self.started_at = datetime.now(UTC)
        self.steps: list[TraceStep] = []

    def step(self, name: str, started: float, **data: Any) -> None:
        """Record a finished step. `started` is a time.perf_counter() reading taken at its start."""
        elapsed = time.perf_counter() - started
        self.steps.append(
            TraceStep(
                name=name,
                # Called when the step ends, so its start is the end minus how long it took.
                started_at=datetime.now(UTC) - timedelta(seconds=elapsed),
                duration_ms=round(elapsed * 1000, 3),
                data=data,
            )
        )
