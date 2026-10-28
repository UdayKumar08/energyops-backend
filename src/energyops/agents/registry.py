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

from energyops.agents.anomaly_investigator import (
    ANOMALY_INVESTIGATOR_SPEC,
    AnomalyInvestigator,
    InvestigationRequest,
)
from energyops.agents.anomaly_investigator_evals import event_sheet_for, good_investigation_reply
from energyops.agents.energy_analyst import ENERGY_ANALYST_SPEC, AnalystRequest, EnergyAnalyst
from energyops.agents.energy_analyst_evals import good_reply
from energyops.agents.event_facts import DirectEventFacts, McpEventFacts
from energyops.agents.fact_sources import DirectFactSheets, McpFactSheets, McpTarget
from energyops.agents.pipeline import AgentRequest, GroundedAgent, HasVia
from energyops.agents.report_writer import REPORT_WRITER_SPEC, ReportRequest, ReportWriter
from energyops.agents.report_writer_evals import good_report_reply
from energyops.agents.savings_advisor import SAVINGS_ADVISOR_SPEC, SavingsAdvisor, SavingsRequest
from energyops.agents.savings_advisor_evals import good_savings_reply, savings_sheet_for
from energyops.agents.savings_facts import DirectSavingsFacts, McpSavingsFacts
from energyops.agents.sources import ReadingsSource, TariffSource
from energyops.agents.spec import AgentSpec
from energyops.facts import build_fact_sheet
from energyops.models.base import ModelClient

MAX_AGENTS = 5
InputKind = Literal["question", "audience", "anomaly", "none"]
DataAccess = Literal["mcp", "direct"]


class InvalidRunInput(ValueError):
    """The run request does not fit what this agent takes."""


@dataclass(frozen=True, slots=True)
class DataSources:
    """Where an agent's facts come from for one run: readings, tariffs and, for the MCP path, a
    server to call. The caller decides what these are; an agent cannot widen them."""

    readings: ReadingsSource
    tariffs: TariffSource
    mcp_target: McpTarget | None = None


@dataclass(frozen=True, slots=True)
class RunInputs:
    """Everything a person can supply for a run. Each agent uses only what it takes."""

    site: str | None = None
    question: str | None = None
    audience: str | None = None
    anomaly_id: str | None = None


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
    build: Callable[[AgentSpec, ModelClient, Any], GroundedAgent[Any, Any, Any]]
    make_request: Callable[[RunInputs], AgentRequest]
    # How the agent gets its facts: directly in Python or through the read-only MCP server.
    make_facts: Callable[[AgentSpec, DataAccess, DataSources], HasVia]
    # The canned correct answer the labelled test adapter gives for this request.
    test_reply: Callable[[DataSources, AgentRequest], str]


def _only(inputs: RunInputs, takes: str) -> None:
    """Refuse anything the agent does not take, naming what it does take."""
    given = {
        "question": inputs.question,
        "audience": inputs.audience,
        "anomaly_id": inputs.anomaly_id,
    }
    extra = [name for name, value in given.items() if value is not None and name != takes]
    if extra:
        what = "only a site" if takes == "none" else takes.replace("_", " ")
        raise InvalidRunInput(f"This agent takes {what}, not {extra[0].replace('_', ' ')}.")


def _site_facts(spec: AgentSpec, access: DataAccess, data: DataSources) -> HasVia:
    if access == "mcp":
        return McpFactSheets(spec, data.mcp_target)
    return DirectFactSheets(data.readings)


def _event_facts(spec: AgentSpec, access: DataAccess, data: DataSources) -> HasVia:
    if access == "mcp":
        return McpEventFacts(spec, data.mcp_target)
    return DirectEventFacts(data.readings)


def _savings_facts(spec: AgentSpec, access: DataAccess, data: DataSources) -> HasVia:
    if access == "mcp":
        return McpSavingsFacts(spec, data.mcp_target)
    return DirectSavingsFacts(data.readings, data.tariffs)


def _analyst_request(inputs: RunInputs) -> AgentRequest:
    _only(inputs, "question")
    if not inputs.question:
        raise InvalidRunInput("This agent needs a question.")
    return AnalystRequest(question=inputs.question, site=inputs.site)


def _report_request(inputs: RunInputs) -> AgentRequest:
    _only(inputs, "audience")
    if inputs.audience is None:
        return ReportRequest(site=inputs.site)
    try:
        return ReportRequest.model_validate({"site": inputs.site, "audience": inputs.audience})
    except ValueError:
        raise InvalidRunInput("The audience must be 'manager' or 'technical'.") from None


def _investigation_request(inputs: RunInputs) -> AgentRequest:
    _only(inputs, "anomaly_id")
    if not inputs.anomaly_id:
        raise InvalidRunInput("This agent needs the id of an event, such as A1.")
    try:
        return InvestigationRequest.model_validate(
            {"site": inputs.site, "anomaly_id": inputs.anomaly_id}
        )
    except ValueError:
        raise InvalidRunInput("An event id looks like A1.") from None


def _savings_request(inputs: RunInputs) -> AgentRequest:
    _only(inputs, "none")
    return SavingsRequest(site=inputs.site)


def _analyst_reply(data: DataSources, request: AgentRequest) -> str:
    return good_reply(build_fact_sheet(data.readings(request.site)))


def _report_reply(data: DataSources, request: AgentRequest) -> str:
    return good_report_reply(build_fact_sheet(data.readings(request.site)))


def _investigation_reply(data: DataSources, request: AgentRequest) -> str:
    assert isinstance(request, InvestigationRequest)
    return good_investigation_reply(
        event_sheet_for(data.readings, request.site, request.anomaly_id)
    )


def _savings_reply(data: DataSources, request: AgentRequest) -> str:
    return good_savings_reply(savings_sheet_for(data.readings, data.tariffs, request.site))


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
        make_facts=_site_facts,
        test_reply=_analyst_reply,
    ),
    AgentEntry(
        id="report-writer",
        spec=REPORT_WRITER_SPEC,
        input_kind="audience",
        examples=(Example("For a manager", "manager"), Example("For a technician", "technical")),
        build=ReportWriter,
        make_request=_report_request,
        make_facts=_site_facts,
        test_reply=_report_reply,
    ),
    AgentEntry(
        id="anomaly-investigator",
        spec=ANOMALY_INVESTIGATOR_SPEC,
        input_kind="anomaly",
        examples=(),  # the playground lists the events found for the chosen site instead
        build=AnomalyInvestigator,
        make_request=_investigation_request,
        make_facts=_event_facts,
        test_reply=_investigation_reply,
    ),
    AgentEntry(
        id="savings-advisor",
        spec=SAVINGS_ADVISOR_SPEC,
        input_kind="none",
        examples=(),
        build=SavingsAdvisor,
        make_request=_savings_request,
        make_facts=_savings_facts,
        test_reply=_savings_reply,
    ),
)

if len(AGENT_ENTRIES) > MAX_AGENTS:  # pragma: no cover - guards future edits
    raise RuntimeError(f"At most {MAX_AGENTS} agents are allowed, found {len(AGENT_ENTRIES)}.")

AGENTS: dict[str, AgentEntry] = {entry.id: entry for entry in AGENT_ENTRIES}
