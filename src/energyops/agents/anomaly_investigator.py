"""The anomaly investigator agent.

A detector in plain Python has already found an unusual event (see anomalies.py). This agent
suggests what a person might check, as hypotheses, and never decides that anything is a fault.
It uses the shared grounded pipeline (see pipeline.py) with an event fact sheet instead of a site
fact sheet, so every figure it writes is checked against figures Python computed.

Beyond the figure check, Python enforces what makes a hypothesis a hypothesis: confidence can only
be low or medium because one meter cannot strongly support any cause, every hypothesis must give a
reason for its confidence, "medium" is refused unless that reason names a fact the hypothesis
cites, and wording that states a cause as certain is rejected. Python cannot judge whether a
suggested check is sensible, so a person has to read them.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field

from energyops.agents.event_facts import EventFactsProvider, EventSheet
from energyops.agents.grounding import check_fact_ids, check_text
from energyops.agents.pipeline import AgentRequest, GroundedAgent, Strict, parse_json_reply
from energyops.agents.runs import TraceRecorder
from energyops.agents.spec import AgentConfig, AgentSpec, ModelChoice
from energyops.facts import FactSheet
from energyops.models.providers import DEFAULT_GEMINI_MODEL

# Wording that states a cause as settled. Hypotheses must be phrased as possibilities.
CERTAINTY = re.compile(
    r"\b(definitely|certainly|undoubtedly|proves?|proven|confirmed|faulty|"
    r"is caused by|was caused by|is due to|was due to|the cause is|the cause was)\b",
    re.IGNORECASE,
)
# A suggested check must not tell anyone to change equipment or call anyone out.
ACTION = re.compile(
    r"\b(switch (it |them )?(on|off)|turn (it |them )?(on|off)|shut (it |them )?down|"
    r"reset|reboot|restart|override|disable|call (a|the) (contractor|engineer|electrician))\b",
    re.IGNORECASE,
)


class InvestigationRequest(AgentRequest):
    anomaly_id: str = Field(pattern=r"^A[0-9]{1,3}$")


class Hypothesis(Strict):
    statement: str = Field(min_length=1, max_length=300)
    # No "high": one electricity meter cannot strongly support any cause.
    confidence: Literal["low", "medium"]
    # Why the label was chosen. For "medium" it must point at facts (see grounding_problems).
    confidence_reason: str = Field(min_length=1, max_length=300)
    suggested_check: str = Field(min_length=1, max_length=300)
    fact_ids: list[str] = Field(min_length=1, max_length=6)


class Investigation(Strict):
    summary: str = Field(min_length=1, max_length=600)
    hypotheses: list[Hypothesis] = Field(min_length=1, max_length=4)


ANOMALY_INVESTIGATOR_SPEC = AgentSpec(
    name="EnergyOps Anomaly Investigator",
    domain="energy-anomalies",
    capabilities=("anomaly-investigation", "hypotheses", "suggested-checks"),
    description="Suggests possible causes of a detected energy anomaly as hypotheses to check.",
    config=AgentConfig(
        model=ModelChoice(provider="google-gemini", name=DEFAULT_GEMINI_MODEL),
        prompt_file="anomaly_investigator/v2.md",
        prompt_version="v2",
        max_iterations=2,
        max_output_tokens=1500,
        mcp_servers=("energyops-energy",),
        allowed_tools=("list_sites", "get_fact_sheet", "get_anomalies"),
    ),
)


class AnomalyInvestigator(GroundedAgent[InvestigationRequest, Investigation, EventFactsProvider]):
    async def fetch_facts(self, request: InvestigationRequest, trace: TraceRecorder) -> FactSheet:
        return await self._facts(request.site, request.anomaly_id, trace)

    def input_text(self, request: InvestigationRequest) -> str:
        return ""  # no free text comes from the person

    def user_message(self, request: InvestigationRequest, sheet: FactSheet) -> str:
        assert isinstance(sheet, EventSheet)
        return f"Event facts (the only figures you may quote):\n{sheet.to_prompt_text()}"

    def parse(self, reply: str) -> Investigation:
        return parse_json_reply(
            reply,
            Investigation,
            '{"summary": "...", "hypotheses": [{"statement": "...", "confidence": "low", '
            '"suggested_check": "...", "fact_ids": ["F1"]}]}',
        )

    def grounding_problems(self, output: Investigation, sheet: FactSheet) -> list[str]:
        problems = check_text(output.summary, sheet)
        problems += _certainty(output.summary, "summary")
        for number, item in enumerate(output.hypotheses, start=1):
            label = f"hypothesis {number}"
            problems += check_text(item.statement, sheet) + check_text(item.suggested_check, sheet)
            problems += check_text(item.confidence_reason, sheet)
            problems += check_fact_ids(item.fact_ids, sheet)
            problems += _certainty(item.statement, label) + _certainty(item.suggested_check, label)
            problems += _certainty(item.confidence_reason, label)
            problems += _medium_needs_facts(item, label)
            for match in ACTION.finditer(item.suggested_check):
                problems.append(f"{label}: a check must not tell anyone to '{match.group(0)}'")
        return problems


def _medium_needs_facts(item: Hypothesis, label: str) -> list[str]:
    """ "medium" is only accepted when its reason names a fact (such as F3) that the hypothesis
    itself cites. A model that gives medium to its most generic idea has nothing to point at."""
    if item.confidence != "medium":
        return []
    named = set(re.findall(r"\bF\d+\b", item.confidence_reason))
    if named & set(item.fact_ids):
        return []
    return [
        f"{label}: medium confidence needs a confidence_reason that names one of the fact ids "
        "the hypothesis cites (for example F3), or the confidence must be low"
    ]


def _certainty(text: str, where: str) -> list[str]:
    return [
        f"{where}: '{m.group(0)}' states a cause as certain, write it as a possibility"
        for m in CERTAINTY.finditer(text)
    ]
