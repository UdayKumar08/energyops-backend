"""The report writer agent.

It turns the same computed fact sheet the energy analyst uses into a short written report for
a manager or a technician, on the shared grounded pipeline (see pipeline.py). It has no free
text input: the person picks a site and an audience, so there is nothing to inject through the
request. Besides the figure check, Python enforces the report's structure: known headings, in a
fixed order, each backed by facts, and a data quality section whenever the fact sheet carries a
warning about missing readings.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from energyops.agents.grounding import check_fact_ids, check_text
from energyops.agents.pipeline import AgentRequest, GroundedAgent, Strict, parse_json_reply
from energyops.agents.spec import AgentConfig, AgentSpec, ModelChoice
from energyops.facts import FactSheet
from energyops.models.providers import DEFAULT_GEMINI_MODEL

Audience = Literal["manager", "technical"]
Heading = Literal["Consumption", "Demand", "Weekdays and weekends", "Data quality"]
HEADING_ORDER: tuple[Heading, ...] = (
    "Consumption",
    "Demand",
    "Weekdays and weekends",
    "Data quality",
)


class ReportRequest(AgentRequest):
    audience: Audience = "manager"


class ReportSection(Strict):
    heading: Heading
    body: str = Field(min_length=1, max_length=500)
    fact_ids: list[str] = Field(default_factory=list, max_length=8)


class SiteReport(Strict):
    headline: str = Field(min_length=1, max_length=200)
    sections: list[ReportSection] = Field(min_length=1, max_length=4)


REPORT_WRITER_SPEC = AgentSpec(
    name="EnergyOps Report Writer",
    domain="energy-reporting",
    capabilities=(
        "period-report",
        "consumption-summary",
        "demand-summary",
        "weekday-weekend-comparison",
    ),
    description="Writes a short period report for a manager or technician from computed facts.",
    config=AgentConfig(
        model=ModelChoice(provider="google-gemini", name=DEFAULT_GEMINI_MODEL),
        prompt_file="report_writer/v1.md",
        prompt_version="v1",
        max_iterations=2,
        max_output_tokens=1500,
        mcp_servers=("energyops-energy",),
        allowed_tools=("list_sites", "get_fact_sheet"),
    ),
)


class ReportWriter(GroundedAgent[ReportRequest, SiteReport]):
    def input_text(self, request: ReportRequest) -> str:
        return ""  # no free text comes from the person

    def user_message(self, request: ReportRequest, sheet: FactSheet) -> str:
        return (
            f"Audience: {request.audience}\n\n"
            f"Fact sheet (the only figures you may quote):\n{sheet.to_prompt_text()}"
        )

    def parse(self, reply: str) -> SiteReport:
        return parse_json_reply(
            reply,
            SiteReport,
            '{"headline": "...", "sections": [{"heading": "Consumption", "body": "...", '
            '"fact_ids": ["F1"]}]}',
        )

    def grounding_problems(self, output: SiteReport, sheet: FactSheet) -> list[str]:
        problems = check_text(output.headline, sheet)
        headings = [s.heading for s in output.sections]
        if len(set(headings)) != len(headings):
            problems.append("each section heading may be used only once")
        if headings != sorted(headings, key=HEADING_ORDER.index):
            problems.append("sections must be in this order: " + ", ".join(HEADING_ORDER))
        for section in output.sections:
            problems += check_text(section.body, sheet)
            problems += check_fact_ids(section.fact_ids, sheet)
            if section.heading != "Data quality" and not section.fact_ids:
                problems.append(f"the '{section.heading}' section must cite fact ids")
        has_quality = "Data quality" in headings
        if sheet.warnings and not has_quality:
            problems.append("the fact sheet has a Note, so a 'Data quality' section is required")
        if has_quality and not sheet.warnings:
            problems.append("the fact sheet has no Note, so leave out the 'Data quality' section")
        return problems
