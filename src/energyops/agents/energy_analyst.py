"""The energy analyst agent.

It answers one question about a site using the shared grounded pipeline (see pipeline.py):
Python builds the fact sheet, one model call explains it as JSON, and Python checks the shape
and every figure before anything is returned.
"""

from __future__ import annotations

from pydantic import Field

from energyops.agents.grounding import check_fact_ids, check_text
from energyops.agents.pipeline import (
    AgentRequest,
    GroundedAgent,
    ReplyShapeError,
    Strict,
    parse_json_reply,
)
from energyops.agents.spec import AgentConfig, AgentSpec, ModelChoice
from energyops.facts import FactSheet
from energyops.models.providers import DEFAULT_GEMINI_MODEL

__all__ = [
    "ENERGY_ANALYST_SPEC",
    "AnalystReport",
    "AnalystRequest",
    "EnergyAnalyst",
    "Finding",
    "ReplyShapeError",
]


class AnalystRequest(AgentRequest):
    question: str = Field(min_length=1, max_length=500)


class Finding(Strict):
    text: str = Field(min_length=1, max_length=400)
    fact_ids: list[str] = Field(min_length=1, max_length=8)


class AnalystReport(Strict):
    summary: str = Field(min_length=1, max_length=800)
    findings: list[Finding] = Field(default_factory=list, max_length=6)


ENERGY_ANALYST_SPEC = AgentSpec(
    name="EnergyOps Energy Analyst",
    domain="energy-analysis",
    capabilities=(
        "energy-analysis",
        "consumption-summary",
        "peak-demand",
        "weekday-weekend-comparison",
    ),
    description="Explains how a site used energy over a period using computed facts only.",
    config=AgentConfig(
        model=ModelChoice(provider="google-gemini", name=DEFAULT_GEMINI_MODEL),
        prompt_file="energy_analyst/v3.md",
        prompt_version="v3",
        max_iterations=2,
        mcp_servers=("energyops-energy",),
        allowed_tools=("list_sites", "get_fact_sheet"),
    ),
)


class EnergyAnalyst(GroundedAgent[AnalystRequest, AnalystReport]):
    def input_text(self, request: AnalystRequest) -> str:
        return request.question

    def user_message(self, request: AnalystRequest, sheet: FactSheet) -> str:
        return (
            f"Question: {request.question}\n\n"
            f"Fact sheet (the only figures you may quote):\n{sheet.to_prompt_text()}"
        )

    def parse(self, reply: str) -> AnalystReport:
        return parse_json_reply(reply, AnalystReport, '{"summary": "...", "findings": []}')

    def grounding_problems(self, output: AnalystReport, sheet: FactSheet) -> list[str]:
        problems = check_text(output.summary, sheet)
        for finding in output.findings:
            problems += check_text(finding.text, sheet)
            problems += check_fact_ids(finding.fact_ids, sheet)
        return problems
