"""The savings advisor agent.

Python has already worked out what the site's energy costs and what a few possible changes might
save (see savings.py). This agent picks among those levers and explains them. It follows the
shared grounded pipeline, with a cost sheet in place of a plain fact sheet, so every figure it
writes, money included, is checked against figures Python computed.

Beyond the figure check, Python enforces the honesty of the wording. A saving is always called an
estimate and is always given as a range, never promised, never described as verified or already
achieved. The caveat has to say that the figures are estimates and not guaranteed. The advisor may
not talk about carbon, weather, forecasts or payback, because the facts hold none of them. And an
action may only be proposed for a lever that exists: a site with nothing to estimate gets no
actions. Python cannot judge whether an action is sensible for a particular building, so a person
has to read them.
"""

from __future__ import annotations

import re

from pydantic import Field

from energyops.agents.grounding import check_fact_ids, check_text
from energyops.agents.pipeline import AgentRequest, GroundedAgent, Strict, parse_json_reply
from energyops.agents.runs import TraceRecorder
from energyops.agents.savings_facts import SavingsFactsProvider
from energyops.agents.spec import AgentConfig, AgentSpec, ModelChoice
from energyops.facts import FactSheet
from energyops.models.providers import DEFAULT_GEMINI_MODEL
from energyops.savings import SavingsSheet

ESTIMATE = re.compile(r"\bestimate[sd]?\b", re.IGNORECASE)
NOT_A_PROMISE = re.compile(
    r"\b(not (a )?(guarantee[d]?|promise[d]?|verified)|no guarantee|unverified)\b", re.IGNORECASE
)
PROMISE = re.compile(
    r"\b(will (save|reduce|cut|lower)|guarantee[sd]?|promise[sd]?|proven|verified|certainly|"
    r"definitely|already saved|has saved|have saved|achieved|realised|realized)\b",
    re.IGNORECASE,
)
OUT_OF_SCOPE = re.compile(
    r"\b(carbon|co2|emissions?|weather|forecast(s|ed)?|payback|return on investment|roi|"
    r"grants?|subsid(y|ies))\b",
    re.IGNORECASE,
)
MONEY = re.compile(r"[£€$]\s?\d[\d,]*(?:\.\d+)?")


class SavingsRequest(AgentRequest):
    """Only a site. There is no free text for a person to inject anything through."""


class SavingsAction(Strict):
    title: str = Field(min_length=1, max_length=120)
    explanation: str = Field(min_length=1, max_length=500)
    fact_ids: list[str] = Field(min_length=1, max_length=10)


class SavingsAdvice(Strict):
    summary: str = Field(min_length=1, max_length=600)
    actions: list[SavingsAction] = Field(default_factory=list, max_length=4)
    caveat: str = Field(min_length=1, max_length=300)


SAVINGS_ADVISOR_SPEC = AgentSpec(
    name="EnergyOps Savings Advisor",
    domain="energy-savings",
    capabilities=("cost-estimates", "saving-levers", "estimated-ranges"),
    description=(
        "Suggests where cost might be reduced, with estimated ranges calculated from the "
        "site's tariff. Estimates only, never promises."
    ),
    config=AgentConfig(
        model=ModelChoice(provider="google-gemini", name=DEFAULT_GEMINI_MODEL),
        prompt_file="savings_advisor/v1.md",
        prompt_version="v1",
        max_iterations=2,
        max_output_tokens=1800,
        mcp_servers=("energyops-energy",),
        allowed_tools=("list_sites", "get_savings_facts"),
    ),
)


class SavingsAdvisor(GroundedAgent[SavingsRequest, SavingsAdvice, SavingsFactsProvider]):
    async def fetch_facts(self, request: SavingsRequest, trace: TraceRecorder) -> FactSheet:
        return await self._facts(request.site, trace)

    def input_text(self, request: SavingsRequest) -> str:
        return ""

    def user_message(self, request: SavingsRequest, sheet: FactSheet) -> str:
        return f"Cost facts (the only figures you may quote):\n{sheet.to_prompt_text()}"

    def parse(self, reply: str) -> SavingsAdvice:
        return parse_json_reply(
            reply,
            SavingsAdvice,
            '{"summary": "...", "actions": [{"title": "...", "explanation": "...", '
            '"fact_ids": ["F1"]}], "caveat": "..."}',
        )

    def grounding_problems(self, output: SavingsAdvice, sheet: FactSheet) -> list[str]:
        problems = check_text(output.summary, sheet) + check_text(output.caveat, sheet)
        problems += _wording(output.summary, "the summary")
        problems += _estimate_label(output.summary, "the summary")
        if not (ESTIMATE.search(output.caveat) and NOT_A_PROMISE.search(output.caveat)):
            problems.append(
                "the caveat must say that the figures are estimates and are not guaranteed or "
                "verified savings"
            )
        if OUT_OF_SCOPE.search(output.caveat):
            problems.append("the caveat: " + _scope_message(output.caveat))
        levers = sheet.lever_count if isinstance(sheet, SavingsSheet) else 0
        if levers == 0 and output.actions:
            problems.append("the fact sheet has no saving lever, so give no actions")
        if levers > 0 and not output.actions:
            problems.append("the fact sheet has saving levers, so give at least one action")
        for number, action in enumerate(output.actions, start=1):
            label = f"action {number}"
            text = f"{action.title} {action.explanation}"
            problems += check_text(action.title, sheet) + check_text(action.explanation, sheet)
            problems += check_fact_ids(action.fact_ids, sheet)
            problems += _wording(text, label)
            problems += _estimate_label(text, label)
        return problems


def _wording(text: str, where: str) -> list[str]:
    problems = [
        f"{where}: '{m.group(0)}' promises or claims a saving, write it as an estimate"
        for m in PROMISE.finditer(text)
    ]
    if OUT_OF_SCOPE.search(text):
        problems.append(f"{where}: {_scope_message(text)}")
    return problems


def _scope_message(text: str) -> str:
    match = OUT_OF_SCOPE.search(text)
    word = match.group(0) if match else ""
    return f"'{word}' is outside the facts, leave it out"


def _estimate_label(text: str, where: str) -> list[str]:
    """Money in a sentence must be called an estimate and given as a range."""
    amounts = {m.group(0).replace(" ", "") for m in MONEY.finditer(text)}
    if not amounts:
        return []
    problems = []
    if not ESTIMATE.search(text):
        problems.append(f"{where}: money figures must be called an estimate")
    if len(amounts) < 2 and where != "the summary":
        problems.append(f"{where}: give the range, both the low end and the high end")
    return problems
