"""Evaluation cases for the savings advisor.

The replies are scripted and played through the labelled test adapter, against the invented
demo-hotel site and its invented tariff. They check the agent's own pipeline and its rules about
money (every amount from the sheet, always an estimate, always a range, never a promise). They
say nothing about how good any real model's advice is.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from energyops.agents.runs import RunRecord, RunStatus
from energyops.agents.savings_advisor import SAVINGS_ADVISOR_SPEC, SavingsAdvisor, SavingsRequest
from energyops.agents.savings_facts import DirectSavingsFacts
from energyops.agents.sources import (
    ReadingsSource,
    TariffSource,
    demo_tariff_source,
    synthetic_demo_source,
)
from energyops.facts import FactSheet
from energyops.models.test_adapter import TestAdapter
from energyops.savings import build_savings_sheet
from energyops.tariffs import require_tariff

SITE = "demo-hotel"

CAVEAT = "These are estimates built from stated assumptions, not guaranteed or verified savings."
TITLES = {
    "night": "Look at the always-on night time load",
    "peak": "Look at the highest demand",
    "shift": "Look at moving some use out of the peak band",
    "spikes": "Look at the unusual spikes",
}


def savings_sheet_for(source: ReadingsSource, tariffs: TariffSource, site: str | None) -> FactSheet:
    return build_savings_sheet(source(site), require_tariff(tariffs(site), site))


def good_savings_reply(sheet: FactSheet) -> str:
    """A correct answer for any savings sheet: one action per lever that the sheet holds."""
    by_key = {f.key: f for f in sheet.facts}
    monthly = by_key["monthly_cost"]
    actions = []
    for stem, title in TITLES.items():
        low, high = by_key.get(f"{stem}_saving_cost_low"), by_key.get(f"{stem}_saving_cost_high")
        if low is None or high is None:
            continue
        actions.append(
            {
                "title": title,
                "explanation": (
                    f"This is an estimate: it could be worth between {low.display} and "
                    f"{high.display} in 30 days, and a person would need to check the cause."
                ),
                "fact_ids": [low.id, high.id],
            }
        )
    if actions:
        summary = (
            f"The estimated energy cost is {monthly.display} for 30 days at this rate. "
            "The levers below are estimates for a person to examine."
        )
    else:
        summary = (
            f"The estimated energy cost is {monthly.display} for 30 days at this rate. "
            "No saving lever applies to this site's data."
        )
    return json.dumps({"summary": summary, "actions": actions, "caveat": CAVEAT})


def _reply(
    summary: str = "The levers below are estimates for a person to examine.",
    actions: list[dict[str, object]] | None = None,
    caveat: str = CAVEAT,
) -> str:
    return json.dumps({"summary": summary, "actions": actions or [], "caveat": caveat})


def _action(
    explanation: str = "This is an estimate that a person should check against the site.",
    fact_ids: list[str] | None = None,
    title: str = "Look at the night time load",
) -> dict[str, object]:
    return {"title": title, "explanation": explanation, "fact_ids": fact_ids or ["F1"]}


@dataclass(frozen=True, slots=True)
class SavingsEvalCase:
    name: str
    description: str
    replies: Callable[[FactSheet], tuple[str, ...]]
    expected_status: RunStatus
    expected_attempts: int
    expected_error_kind: str | None = None


def _grounding_case(name: str, description: str, reply: str) -> SavingsEvalCase:
    return SavingsEvalCase(
        name, description, lambda sheet: (reply,), RunStatus.FAILED, 2, "grounding"
    )


CASES: tuple[SavingsEvalCase, ...] = (
    SavingsEvalCase(
        "good_advice",
        "A grounded answer with estimated ranges and an honest caveat passes first time.",
        lambda sheet: (good_savings_reply(sheet),),
        RunStatus.SUCCEEDED,
        1,
    ),
    _grounding_case(
        "invented_money_is_rejected",
        "A currency amount that is not on the sheet is rejected.",
        _reply(actions=[_action("An estimate of between £999.00 and £1,999.00 in 30 days.")]),
    ),
    _grounding_case(
        "one_end_of_a_range_is_rejected",
        "An action that quotes a single money figure, not the range, is rejected.",
        _reply(actions=[_action("An estimate of £56.68 in 30 days.")]),
    ),
    _grounding_case(
        "money_without_the_word_estimate_is_rejected",
        "Money figures must be called an estimate.",
        _reply(actions=[_action("This could be £56.68 to £113.37 in 30 days.")]),
    ),
    SavingsEvalCase(
        "promising_a_saving_is_rejected",
        "'Will save' is rejected, then an honest answer is accepted.",
        lambda sheet: (
            _reply(actions=[_action("This will save an estimated £56.68 to £113.37.")]),
            good_savings_reply(sheet),
        ),
        RunStatus.SUCCEEDED,
        2,
    ),
    _grounding_case(
        "claiming_a_verified_saving_is_rejected",
        "A saving may not be described as verified or achieved.",
        _reply(actions=[_action("A verified estimate of between £56.68 and £113.37.")]),
    ),
    _grounding_case(
        "a_caveat_that_does_not_say_estimate_is_rejected",
        "The caveat has to say the figures are estimates and not guaranteed.",
        _reply(caveat="Good luck with the savings."),
    ),
    _grounding_case(
        "talking_about_carbon_is_rejected",
        "The facts hold no carbon figures, so the topic is refused.",
        _reply(summary="The estimate also cuts carbon."),
    ),
    _grounding_case(
        "amounts_in_words_are_rejected",
        "Money written as '40 pounds' cannot be matched, so it is rejected.",
        _reply(actions=[_action("An estimate of about 40 pounds.")]),
    ),
    _grounding_case(
        "an_unknown_fact_id_is_rejected",
        "Citing a fact id that does not exist is rejected.",
        _reply(actions=[_action(fact_ids=["F99"])]),
    ),
    _grounding_case(
        "no_actions_when_levers_exist_is_rejected",
        "A sheet with saving levers needs at least one action.",
        _reply(actions=[]),
    ),
)


async def run_scripted(case: SavingsEvalCase) -> tuple[bool, RunRecord]:
    """Play one case through the test adapter and say whether the agent behaved as expected."""
    source = synthetic_demo_source()
    tariffs = demo_tariff_source()
    sheet = savings_sheet_for(source, tariffs, SITE)
    agent = SavingsAdvisor(
        SAVINGS_ADVISOR_SPEC,
        TestAdapter(replies=case.replies(sheet)),
        DirectSavingsFacts(source, tariffs),
    )
    record = await agent.run(SavingsRequest(site=SITE))
    passed = (
        record.status is case.expected_status
        and record.attempts == case.expected_attempts
        and record.error_kind == case.expected_error_kind
    )
    return passed, record
