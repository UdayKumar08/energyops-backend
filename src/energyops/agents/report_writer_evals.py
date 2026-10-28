"""Evaluation cases for the report writer.

As with the analyst's cases, the replies are scripted and played through the labelled test
adapter. They check the agent's own pipeline (shape, structure, figures, retry, failure) and
say nothing about how well any real model writes a report.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from energyops.agents.fact_sources import DirectFactSheets
from energyops.agents.report_writer import REPORT_WRITER_SPEC, ReportRequest, ReportWriter
from energyops.agents.runs import RunRecord, RunStatus
from energyops.agents.sources import ReadingsSource, synthetic_demo_source
from energyops.facts import FactSheet, build_fact_sheet
from energyops.models.test_adapter import TestAdapter
from energyops.synthetic import MeterReading


def _reply(headline: str, sections: list[tuple[str, str, list[str]]]) -> str:
    return json.dumps(
        {
            "headline": headline,
            "sections": [{"heading": h, "body": b, "fact_ids": ids} for h, b, ids in sections],
        }
    )


def good_report_reply(sheet: FactSheet) -> str:
    """A correct report for any fact sheet, including one that carries data warnings."""
    total = sheet.by_id("F1")
    peak = next(f for f in sheet.facts if f.key == "peak_demand")
    assert total is not None
    sections = [
        ("Consumption", f"The site used {total.display} in this period.", [total.id]),
        ("Demand", f"Peak demand was {peak.display}.", [peak.id]),
    ]
    if sheet.warnings:
        sections.append(("Data quality", "Some readings were missing, see the note.", []))
    return _reply("The site's energy use for the period.", sections)


def gappy_source() -> ReadingsSource:
    """The demo site with a hole punched in day two, so the fact sheet carries a warning."""
    base = synthetic_demo_source()

    def source(site: str | None) -> Sequence[MeterReading]:
        readings = list(base(site))
        del readings[100:110]
        return readings

    return source


@dataclass(frozen=True, slots=True)
class ReportEvalCase:
    name: str
    description: str
    replies: Callable[[FactSheet], tuple[str, ...]]
    expected_status: RunStatus
    expected_attempts: int
    expected_error_kind: str | None = None
    gappy: bool = False


CASES: tuple[ReportEvalCase, ...] = (
    ReportEvalCase(
        name="good_report",
        description="A correct report passes on the first attempt.",
        replies=lambda sheet: (good_report_reply(sheet),),
        expected_status=RunStatus.SUCCEEDED,
        expected_attempts=1,
    ),
    ReportEvalCase(
        name="invented_figure_is_rejected",
        description="A kWh figure that is not in the fact sheet fails after the retry.",
        replies=lambda sheet: (_reply("Usage.", [("Consumption", "We used 999.9 kWh.", ["F1"])]),),
        expected_status=RunStatus.FAILED,
        expected_attempts=2,
        expected_error_kind="grounding",
    ),
    ReportEvalCase(
        name="cost_section_is_rejected",
        description="A section heading outside the fixed set is a shape error.",
        replies=lambda sheet: (_reply("Costs.", [("Cost", "It was cheap.", ["F1"])]),),
        expected_status=RunStatus.FAILED,
        expected_attempts=2,
        expected_error_kind="invalid_output",
    ),
    ReportEvalCase(
        name="wrong_section_order_is_corrected",
        description="Sections out of order are sent back and then accepted.",
        replies=lambda sheet: (
            _reply(
                "Order.",
                [("Demand", "Peak was high.", ["F1"]), ("Consumption", "Used energy.", ["F1"])],
            ),
            good_report_reply(sheet),
        ),
        expected_status=RunStatus.SUCCEEDED,
        expected_attempts=2,
    ),
    ReportEvalCase(
        name="section_without_facts_is_rejected",
        description="A section that cites no fact ids fails after the retry.",
        replies=lambda sheet: (_reply("Nothing cited.", [("Consumption", "Used energy.", [])]),),
        expected_status=RunStatus.FAILED,
        expected_attempts=2,
        expected_error_kind="grounding",
    ),
    ReportEvalCase(
        name="missing_data_quality_section_is_corrected",
        description="When readings are missing the report must say so; the retry adds it.",
        replies=lambda sheet: (
            _reply("Short.", [("Consumption", "Used energy.", ["F1"])]),
            good_report_reply(sheet),
        ),
        expected_status=RunStatus.SUCCEEDED,
        expected_attempts=2,
        gappy=True,
    ),
    ReportEvalCase(
        name="needless_data_quality_section_is_rejected",
        description="A data quality section for clean data is not allowed.",
        replies=lambda sheet: (
            _reply(
                "Clean.",
                [("Consumption", "Used energy.", ["F1"]), ("Data quality", "Gaps.", [])],
            ),
        ),
        expected_status=RunStatus.FAILED,
        expected_attempts=2,
        expected_error_kind="grounding",
    ),
    ReportEvalCase(
        name="wrong_day_count_is_rejected",
        description="A day count that disagrees with the days-covered fact is rejected.",
        replies=lambda sheet: (
            _reply("Over the 13-day period.", [("Consumption", "Used energy.", ["F1"])]),
        ),
        expected_status=RunStatus.FAILED,
        expected_attempts=2,
        expected_error_kind="grounding",
    ),
)


async def run_scripted(case: ReportEvalCase) -> tuple[bool, RunRecord]:
    """Play one case through the test adapter and say whether the agent behaved as expected."""
    source = gappy_source() if case.gappy else synthetic_demo_source()
    sheet = build_fact_sheet(source(None))
    agent = ReportWriter(
        REPORT_WRITER_SPEC, TestAdapter(replies=case.replies(sheet)), DirectFactSheets(source)
    )
    record = await agent.run(ReportRequest())
    passed = (
        record.status is case.expected_status
        and record.attempts == case.expected_attempts
        and record.error_kind == case.expected_error_kind
    )
    return passed, record
