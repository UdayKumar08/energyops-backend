"""Small types shared by the registry and the supervisor, so neither has to import the other."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RunInputs:
    """Everything a person can supply for a run. Each agent uses only what it takes."""

    site: str | None = None
    question: str | None = None
    audience: str | None = None
    anomaly_id: str | None = None
