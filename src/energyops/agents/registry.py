"""The agents this product has, in one place. At most five (see docs/agents.md).

The local API and, later, the admin screens read this list instead of importing each agent, so
adding an agent means adding one entry here. An entry says how to build the agent, what a run
needs from the person (a question, or just an audience), and which canned reply the labelled
test adapter should give so the agent can be tried without a real model.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from energyops.agents.energy_analyst import ENERGY_ANALYST_SPEC, AnalystRequest, EnergyAnalyst
from energyops.agents.energy_analyst_evals import good_reply
from energyops.agents.fact_sources import FactSheetProvider
from energyops.agents.pipeline import AgentRequest, GroundedAgent
from energyops.agents.report_writer import REPORT_WRITER_SPEC, ReportRequest, ReportWriter
from energyops.agents.report_writer_evals import good_report_reply
from energyops.agents.spec import AgentSpec
from energyops.facts import FactSheet
from energyops.models.base import ModelClient

MAX_AGENTS = 5
InputKind = Literal["question", "audience"]


class InvalidRunInput(ValueError):
    """The run request does not fit what this agent takes."""


@dataclass(frozen=True, slots=True)
class RunInputs:
    """Everything a person can supply for a run. Each agent uses only what it takes."""

    site: str | None = None
    question: str | None = None
    audience: str | None = None


@dataclass(frozen=True, slots=True)
class Example:
    label: str
    value: str


@dataclass(frozen=True, slots=True)
class AgentEntry:
    id: str
    spec: AgentSpec
    input_kind: InputKind
    examples: tuple[Example, ...]
    build: Callable[[AgentSpec, ModelClient, FactSheetProvider], GroundedAgent[Any, Any, Any]]
    make_request: Callable[[RunInputs], AgentRequest]
    test_reply: Callable[[FactSheet], str]


def _analyst_request(inputs: RunInputs) -> AgentRequest:
    if not inputs.question:
        raise InvalidRunInput("This agent needs a question.")
    if inputs.audience is not None:
        raise InvalidRunInput("This agent takes a question, not an audience.")
    return AnalystRequest(question=inputs.question, site=inputs.site)


def _report_request(inputs: RunInputs) -> AgentRequest:
    if inputs.question is not None:
        raise InvalidRunInput("This agent takes an audience, not a question.")
    if inputs.audience is None:
        return ReportRequest(site=inputs.site)
    try:
        return ReportRequest.model_validate({"site": inputs.site, "audience": inputs.audience})
    except ValueError:
        raise InvalidRunInput("The audience must be 'manager' or 'technical'.") from None


AGENT_ENTRIES: tuple[AgentEntry, ...] = (
    AgentEntry(
        id="energy-analyst",
        spec=ENERGY_ANALYST_SPEC,
        input_kind="question",
        examples=(
            Example("Summary", "Summarise this site's energy use over the period."),
            Example("Peak", "What was the peak demand, and when did it happen?"),
            Example("Weekends", "How do weekends compare with weekdays?"),
            Example("Cost trap", "How much did this cost us, and how much could we save?"),
            Example("Forecast trap", "What will next week's demand be?"),
        ),
        build=EnergyAnalyst,
        make_request=_analyst_request,
        test_reply=good_reply,
    ),
    AgentEntry(
        id="report-writer",
        spec=REPORT_WRITER_SPEC,
        input_kind="audience",
        examples=(Example("For a manager", "manager"), Example("For a technician", "technical")),
        build=ReportWriter,
        make_request=_report_request,
        test_reply=good_report_reply,
    ),
)

if len(AGENT_ENTRIES) > MAX_AGENTS:  # pragma: no cover - guards future edits
    raise RuntimeError(f"At most {MAX_AGENTS} agents are allowed, found {len(AGENT_ENTRIES)}.")

AGENTS: dict[str, AgentEntry] = {entry.id: entry for entry in AGENT_ENTRIES}
