"""The shapes the APIs send about agents and runs, shared by the local playground API and the
platform API so both describe an agent and a run the same way."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from energyops.agents.registry import AgentEntry
from energyops.agents.runs import RunRecord, RunStatus
from energyops.agents.spec import AgentSpec


class ExampleView(BaseModel):
    label: str
    value: str


class AgentSummary(BaseModel):
    id: str
    name: str
    domain: str
    status: str
    description: str
    capabilities: tuple[str, ...]
    model_provider: str
    model_name: str
    prompt_version: str
    # "question": a free-text question. "audience": who a report is for. "anomaly": an event id.
    # "none": only a site.
    input_kind: str
    examples: list[ExampleView]


class PromptView(BaseModel):
    version: str
    sha256: str
    text: str


class AgentDetail(BaseModel):
    summary: AgentSummary
    spec: dict[str, Any]
    prompt: PromptView
    guardrails_not_implemented: list[str]


class RunSummary(BaseModel):
    run_id: str
    agent: str
    status: RunStatus
    question: str
    model_provider: str
    model_name: str
    attempts: int
    error_kind: str | None
    started_at: str
    finished_at: str


def summarise(entry: AgentEntry, spec: AgentSpec | None = None) -> AgentSummary:
    """An agent for a list. `spec` is the spec with admin settings applied, when they differ."""
    spec = spec or entry.spec
    return AgentSummary(
        id=entry.id,
        name=spec.name,
        domain=spec.domain,
        status=spec.status.value,
        description=spec.description,
        capabilities=spec.capabilities,
        model_provider=spec.config.model.provider,
        model_name=spec.config.model.name,
        prompt_version=spec.config.prompt_version,
        input_kind=entry.input_kind,
        examples=[ExampleView(label=e.label, value=e.value) for e in entry.examples],
    )


def input_label(record: RunRecord) -> str:
    """What was asked, for a list of runs: the question, or the audience of a report."""
    question = record.input.get("question")
    if question:
        return str(question)
    audience = record.input.get("audience")
    if audience:
        return f"Report for {audience}"
    site = record.input.get("site") or "the default site"
    event = record.input.get("anomaly_id")
    return f"Investigate {event} at {site}" if event else f"Run for {site}"


def run_summary(record: RunRecord) -> RunSummary:
    return RunSummary(
        run_id=record.run_id,
        agent=record.agent,
        status=record.status,
        question=input_label(record),
        model_provider=record.model_provider,
        model_name=record.model_name,
        attempts=record.attempts,
        error_kind=record.error_kind,
        started_at=record.started_at.isoformat(),
        finished_at=record.finished_at.isoformat(),
    )
