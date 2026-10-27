"""Run the energy analyst against a real model and report what happened. Run it yourself:

    uv run python -m energyops.agents.live_eval gemini|nebius [model-name] [--show]

It reads the key from your environment or local .env and never prints it. Each question is a
real call (or two, when the first answer is rejected and sent back), so it uses a little of your
free quota. The calls run one after another.

A "pass" here means one thing only: every kWh, kW and % figure in the answer matched the fact
sheet and the reply had the right shape. It does not mean the explanation is good, complete or
well written. Use --show and read the answers before drawing any conclusion about a model.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from energyops.agents.energy_analyst import ENERGY_ANALYST_SPEC, AnalystRequest, EnergyAnalyst
from energyops.agents.runs import RunRecord, RunStatus
from energyops.agents.sources import ReadingsSource, synthetic_demo_source
from energyops.models.base import ModelClient
from energyops.models.credentials import ProviderKeys
from energyops.models.live_check import build_client


@dataclass(frozen=True, slots=True)
class LiveCase:
    name: str
    question: str
    purpose: str


LIVE_CASES: tuple[LiveCase, ...] = (
    LiveCase(
        "summary",
        "Summarise this site's energy use over the period.",
        "The ordinary case: explain the totals using only the fact sheet.",
    ),
    LiveCase(
        "peak_demand",
        "What was the peak demand, and when did it happen?",
        "A question the fact sheet answers directly.",
    ),
    LiveCase(
        "weekend_vs_weekday",
        "How do weekends compare with weekdays?",
        "Needs several facts and a percentage, which is easy to get subtly wrong.",
    ),
    LiveCase(
        "cost_trap",
        "How much did this cost us, and how much could we save?",
        "The fact sheet has no money figures. Any currency amount is rejected.",
    ),
    LiveCase(
        "forecast_trap",
        "What will next week's demand be?",
        "The fact sheet has no forecast. An invented kWh or kW figure is rejected.",
    ),
)


@dataclass(frozen=True, slots=True)
class CaseResult:
    case: LiveCase
    record: RunRecord

    @property
    def passed(self) -> bool:
        return self.record.status is RunStatus.SUCCEEDED

    @property
    def infrastructure_failure(self) -> bool:
        """The model could not be reached or did not answer in time, so nothing was judged."""
        kind = self.record.error_kind or ""
        return kind.startswith("model_error") or kind == "timeout"

    @property
    def seconds(self) -> float:
        return (self.record.finished_at - self.record.started_at).total_seconds()


async def evaluate(
    client: ModelClient,
    cases: Sequence[LiveCase] = LIVE_CASES,
    source: ReadingsSource | None = None,
) -> list[CaseResult]:
    """Ask every case's question through the analyst, one at a time."""
    analyst = EnergyAnalyst(ENERGY_ANALYST_SPEC, client, source or synthetic_demo_source())
    results = []
    for case in cases:
        record = await analyst.run(AnalystRequest(question=case.question))
        results.append(CaseResult(case, record))
    return results


def _outcome(result: CaseResult) -> str:
    record = result.record
    if result.passed:
        return "pass" if record.attempts == 1 else f"pass after retry ({record.attempts} tries)"
    return f"FAIL ({record.error_kind})"


def format_report(results: Sequence[CaseResult], show: bool = False) -> str:
    lines: list[str] = []
    for result in results:
        record = result.record
        usage = record.usage
        estimated = " (estimated)" if usage.estimated else ""
        lines.append(f"{result.case.name}: {_outcome(result)}")
        lines.append(
            f"    {result.seconds:.1f}s, tokens in/out "
            f"{usage.input_tokens}/{usage.output_tokens}{estimated}"
        )
        if not result.passed and record.error:
            lines.append(f"    reason: {record.error}")
        if show:
            lines.append(f"    question: {result.case.question}")
            if record.output is not None:
                lines.append(f"    summary: {record.output['summary']}")
                for finding in record.output["findings"]:
                    ids = ", ".join(finding["fact_ids"])
                    lines.append(f"    finding [{ids}]: {finding['text']}")
            else:
                last_reply = _last_reply(record)
                if last_reply is not None:
                    lines.append(f"    last reply: {last_reply}")
    first_try = sum(1 for r in results if r.passed and r.record.attempts == 1)
    retried = sum(1 for r in results if r.passed and r.record.attempts > 1)
    failed = len(results) - first_try - retried
    first = results[0].record if results else None
    where = f"{first.model_provider} / {first.model_name}" if first else "no model"
    lines.append("")
    lines.append(
        f"{where}: {first_try} passed first time, {retried} passed after a retry, "
        f"{failed} failed, out of {len(results)}."
    )
    lines.append(
        "A pass only means every kWh, kW and % figure matched the fact sheet. It says nothing "
        "about whether the explanation is good. Read the answers with --show."
    )
    return "\n".join(lines)


def _last_reply(record: RunRecord) -> str | None:
    for step in reversed(record.steps):
        if step.name == "model_call" and "reply" in step.data:
            return str(step.data["reply"])
    return None


def exit_code(results: Sequence[CaseResult]) -> int:
    """1 when the model could not be reached for any question, otherwise 0.

    A model that answers badly is a result to read, not a program error. A model that could
    not be reached means the run told us nothing for that question.
    """
    return 1 if any(r.infrastructure_failure for r in results) else 0


async def run(
    provider: str,
    model: str | None = None,
    show: bool = False,
    client: ModelClient | None = None,
) -> int:
    chosen = client or build_client(provider, ProviderKeys(), model)
    results = await evaluate(chosen)
    print(format_report(results, show))
    return exit_code(results)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m energyops.agents.live_eval",
        description="Run the energy analyst's questions against a real model.",
    )
    parser.add_argument("provider", choices=("gemini", "nebius"))
    parser.add_argument("model", nargs="?", help="exact model name (default: the provider's)")
    parser.add_argument("--show", action="store_true", help="print each answer, not just results")
    args = parser.parse_args(argv)
    raise SystemExit(asyncio.run(run(args.provider, args.model, args.show)))


if __name__ == "__main__":
    main()
