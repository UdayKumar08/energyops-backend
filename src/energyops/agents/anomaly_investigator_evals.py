"""Evaluation cases for the anomaly investigator.

The replies are scripted and played through the labelled test adapter, against the invented
demo-clinic site and its first event. They check the agent's own pipeline and its rules about
hypotheses (figures, confidence, certain wording, instructions to change equipment). They say
nothing about how good any real model's hypotheses are.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from energyops.agents.anomaly_investigator import (
    ANOMALY_INVESTIGATOR_SPEC,
    AnomalyInvestigator,
    InvestigationRequest,
)
from energyops.agents.event_facts import DirectEventFacts, EventSheet, build_event_sheet, pick_event
from energyops.agents.runs import RunRecord, RunStatus
from energyops.agents.sources import ReadingsSource, synthetic_demo_source
from energyops.anomalies import detect_anomalies
from energyops.facts import FactSheet, build_fact_sheet
from energyops.mcp_servers.schema import AnomalyListModel
from energyops.models.test_adapter import TestAdapter

SITE = "demo-clinic"
EVENT = "A1"


def event_sheet_for(source: ReadingsSource, site: str | None, anomaly_id: str) -> EventSheet:
    readings = source(site)
    events = AnomalyListModel.from_result(detect_anomalies(readings))
    return build_event_sheet(build_fact_sheet(readings), pick_event(events, anomaly_id))


def _reply(summary: str, hypotheses: list[dict[str, object]]) -> str:
    return json.dumps({"summary": summary, "hypotheses": hypotheses})


def _hypothesis(
    statement: str = "One possibility is equipment running outside its schedule.",
    confidence: str = "low",
    check: str = "Compare the start and end with the building's operating schedule.",
    fact_ids: list[str] | None = None,
) -> dict[str, object]:
    return {
        "statement": statement,
        "confidence": confidence,
        "suggested_check": check,
        "fact_ids": fact_ids or ["F1"],
    }


def good_investigation_reply(sheet: FactSheet) -> str:
    """A correct answer for any event sheet."""
    energy = sheet.by_id("F1")
    duration = next(f for f in sheet.facts if f.key == "event_duration")
    assert energy is not None
    return _reply(
        f"Demand was unusual for {duration.display}, a difference of {energy.display} in energy.",
        [_hypothesis(fact_ids=[energy.id, duration.id])],
    )


@dataclass(frozen=True, slots=True)
class InvestigationEvalCase:
    name: str
    description: str
    replies: Callable[[FactSheet], tuple[str, ...]]
    expected_status: RunStatus
    expected_attempts: int
    expected_error_kind: str | None = None


CASES: tuple[InvestigationEvalCase, ...] = (
    InvestigationEvalCase(
        "good_investigation",
        "A grounded answer with hedged hypotheses passes on the first attempt.",
        lambda sheet: (good_investigation_reply(sheet),),
        RunStatus.SUCCEEDED,
        1,
    ),
    InvestigationEvalCase(
        "invented_figure_is_rejected",
        "A kWh figure that is not in the event sheet fails after the retry.",
        lambda sheet: (_reply("It used 999.9 kWh extra.", [_hypothesis()]),),
        RunStatus.FAILED,
        2,
        "grounding",
    ),
    InvestigationEvalCase(
        "wrong_duration_is_rejected",
        "A duration in minutes that is not the event's duration is rejected.",
        lambda sheet: (_reply("It lasted 7 minutes.", [_hypothesis()]),),
        RunStatus.FAILED,
        2,
        "grounding",
    ),
    InvestigationEvalCase(
        "high_confidence_is_a_shape_error",
        "Confidence can only be low or medium, so 'high' is sent back as a shape error.",
        lambda sheet: (_reply("Unusual.", [_hypothesis(confidence="high")]),),
        RunStatus.FAILED,
        2,
        "invalid_output",
    ),
    InvestigationEvalCase(
        "stating_a_cause_as_certain_is_rejected",
        "Wording that settles the cause is rejected, then a hedged answer is accepted.",
        lambda sheet: (
            _reply("Unusual.", [_hypothesis(statement="The heating is definitely the cause.")]),
            good_investigation_reply(sheet),
        ),
        RunStatus.SUCCEEDED,
        2,
    ),
    InvestigationEvalCase(
        "telling_someone_to_change_equipment_is_rejected",
        "A suggested check may not say to switch things off or call a contractor.",
        lambda sheet: (
            _reply("Unusual.", [_hypothesis(check="Switch it off and call a contractor.")]),
        ),
        RunStatus.FAILED,
        2,
        "grounding",
    ),
    InvestigationEvalCase(
        "unknown_fact_id_is_rejected",
        "Citing a fact id that does not exist fails after the retry.",
        lambda sheet: (_reply("Unusual.", [_hypothesis(fact_ids=["F99"])]),),
        RunStatus.FAILED,
        2,
        "grounding",
    ),
    InvestigationEvalCase(
        "no_hypotheses_is_a_shape_error",
        "At least one hypothesis is required.",
        lambda sheet: (_reply("Unusual.", []),),
        RunStatus.FAILED,
        2,
        "invalid_output",
    ),
    InvestigationEvalCase(
        "cost_figure_is_rejected",
        "Money figures are rejected because the event sheet holds none.",
        lambda sheet: (_reply("That cost about £40.", [_hypothesis()]),),
        RunStatus.FAILED,
        2,
        "grounding",
    ),
)


async def run_scripted(case: InvestigationEvalCase) -> tuple[bool, RunRecord]:
    """Play one case through the test adapter and say whether the agent behaved as expected."""
    source = synthetic_demo_source()
    sheet = event_sheet_for(source, SITE, EVENT)
    agent = AnomalyInvestigator(
        ANOMALY_INVESTIGATOR_SPEC,
        TestAdapter(replies=case.replies(sheet)),
        DirectEventFacts(source),
    )
    record = await agent.run(InvestigationRequest(site=SITE, anomaly_id=EVENT))
    passed = (
        record.status is case.expected_status
        and record.attempts == case.expected_attempts
        and record.error_kind == case.expected_error_kind
    )
    return passed, record
