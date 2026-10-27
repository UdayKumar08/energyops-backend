"""Evaluation cases for the energy analyst.

Each case says what the agent must do with a given model reply. Here the replies are scripted
and played through the labelled test adapter, so these cases check the agent's own pipeline
(shape check, figure check, retry, failure) and say nothing about how good any real model is.
A later command will run the questions against a real provider and report that separately.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from energyops.agents.energy_analyst import ENERGY_ANALYST_SPEC, AnalystRequest, EnergyAnalyst
from energyops.agents.fact_sources import DirectFactSheets
from energyops.agents.runs import RunRecord, RunStatus
from energyops.agents.sources import synthetic_demo_source
from energyops.facts import FactSheet, build_fact_sheet
from energyops.models.test_adapter import TestAdapter


def _reply(summary: str, findings: list[tuple[str, list[str]]]) -> str:
    return json.dumps(
        {"summary": summary, "findings": [{"text": t, "fact_ids": ids} for t, ids in findings]}
    )


def good_reply(sheet: FactSheet) -> str:
    total = sheet.by_id("F1")
    peak = next(f for f in sheet.facts if f.key == "peak_demand")
    assert total is not None
    return _reply(
        f"The site used {total.display} in this period.",
        [(f"Peak demand was {peak.display}.", [peak.id])],
    )


@dataclass(frozen=True, slots=True)
class EvalCase:
    name: str
    description: str
    question: str
    replies: Callable[[FactSheet], tuple[str, ...]]
    expected_status: RunStatus
    expected_attempts: int
    expected_error_kind: str | None = None


CASES: tuple[EvalCase, ...] = (
    EvalCase(
        name="summarise_the_period",
        description="A correct answer passes on the first attempt.",
        question="Summarise this site's energy use.",
        replies=lambda sheet: (good_reply(sheet),),
        expected_status=RunStatus.SUCCEEDED,
        expected_attempts=1,
    ),
    EvalCase(
        name="invented_energy_figure_is_rejected",
        description="A kWh figure that is not in the fact sheet fails after the retry.",
        question="How much energy did we use?",
        replies=lambda sheet: (_reply("We used 999.9 kWh.", [("Usage was 999.9 kWh.", ["F1"])]),),
        expected_status=RunStatus.FAILED,
        expected_attempts=2,
        expected_error_kind="grounding",
    ),
    EvalCase(
        name="recovers_after_correction",
        description="A wrong figure followed by a correct answer succeeds on the second attempt.",
        question="How much energy did we use?",
        replies=lambda sheet: (
            _reply("We used 999.9 kWh.", [("Usage was 999.9 kWh.", ["F1"])]),
            good_reply(sheet),
        ),
        expected_status=RunStatus.SUCCEEDED,
        expected_attempts=2,
    ),
    EvalCase(
        name="malformed_reply_then_valid",
        description="A reply that is not JSON is sent back once and then accepted.",
        question="Give me a summary.",
        replies=lambda sheet: ("Sure! Here is a summary in plain text.", good_reply(sheet)),
        expected_status=RunStatus.SUCCEEDED,
        expected_attempts=2,
    ),
    EvalCase(
        name="cost_claim_is_rejected",
        description="Money figures are rejected because the fact sheet holds no cost data.",
        question="How much did this cost us?",
        replies=lambda sheet: (
            _reply("This cost about £120 last week.", [("Cost was £120.", ["F1"])]),
        ),
        expected_status=RunStatus.FAILED,
        expected_attempts=2,
        expected_error_kind="grounding",
    ),
    EvalCase(
        name="unknown_fact_id_is_rejected",
        description="Citing a fact id that does not exist fails after the retry.",
        question="What was the peak?",
        replies=lambda sheet: (
            _reply("Peak demand is described below.", [("See the peak figure.", ["F99"])]),
        ),
        expected_status=RunStatus.FAILED,
        expected_attempts=2,
        expected_error_kind="grounding",
    ),
    EvalCase(
        name="too_many_findings_is_corrected",
        description="Valid JSON with more than six findings is sent back and then accepted.",
        question="Summarise this site's energy use.",
        replies=lambda sheet: (
            _reply("Everything.", [(f"Fact {i}.", ["F1"]) for i in range(9)]),
            good_reply(sheet),
        ),
        expected_status=RunStatus.SUCCEEDED,
        expected_attempts=2,
    ),
    EvalCase(
        name="wrong_day_count_is_rejected",
        description="Found in the second live run: '13-day period' for 14 days of data.",
        question="Summarise this site's energy use.",
        replies=lambda sheet: (_reply("Over the 13-day period the site used energy.", []),),
        expected_status=RunStatus.FAILED,
        expected_attempts=2,
        expected_error_kind="grounding",
    ),
)


async def run_scripted(case: EvalCase) -> tuple[bool, RunRecord]:
    """Play one case through the test adapter and say whether the agent behaved as expected."""
    source = synthetic_demo_source()
    sheet = build_fact_sheet(source(None))
    agent = EnergyAnalyst(
        ENERGY_ANALYST_SPEC, TestAdapter(replies=case.replies(sheet)), DirectFactSheets(source)
    )
    record = await agent.run(AnalystRequest(question=case.question))
    passed = (
        record.status is case.expected_status
        and record.attempts == case.expected_attempts
        and record.error_kind == case.expected_error_kind
    )
    return passed, record
