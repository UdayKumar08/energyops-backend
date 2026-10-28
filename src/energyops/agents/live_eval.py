"""Run the energy analyst against a real model and report what happened. Run it yourself:

    uv run python -m energyops.agents.live_eval gemini|nebius [model-name] [--show]
        [--save FILE] [--via mcp]
        [--agent energy-analyst|report-writer|anomaly-investigator|savings-advisor]

It reads the key from your environment or local .env and never prints it. Each question is a
real call (or two, when the first answer is rejected and sent back), so it uses a little of your
free quota. The calls run one after another.

A "pass" here means one thing only: every kWh, kW and % figure in the answer matched the fact
sheet and the reply had the right shape. It does not mean the explanation is good, complete or
well written. Use --show and read the answers before drawing any conclusion about a model.

Copying long lines out of a terminal can silently drop a space at each wrap point, which looks
like a typo in the model's answer. To share or read results reliably, use --save FILE: it writes
the full run records (answers, rejection reasons, token counts) as JSON. The file never
contains an API key.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from energyops.agents.pipeline import HasVia
from energyops.agents.registry import AGENTS, DataSources, RunInputs
from energyops.agents.runs import RunRecord, RunStatus
from energyops.agents.sources import demo_tariff_source, synthetic_demo_source
from energyops.models.base import ModelClient
from energyops.models.credentials import ProviderKeys
from energyops.models.live_check import build_client


@dataclass(frozen=True, slots=True)
class LiveCase:
    """One thing to ask. For the report writer there is no question: `question` is then only a
    label for the output, and `audience` and `site` are what is actually sent."""

    name: str
    question: str
    purpose: str
    audience: str | None = None
    site: str | None = None
    anomaly_id: str | None = None


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


REPORT_CASES: tuple[LiveCase, ...] = (
    LiveCase(
        "manager_hotel",
        "Report for a manager, demo-hotel",
        "The ordinary case: a plain-language report with every figure from the fact sheet.",
        audience="manager",
        site="demo-hotel",
    ),
    LiveCase(
        "technical_hotel",
        "Report for a technician, demo-hotel",
        "The technical audience: precise units and the time of the peak.",
        audience="technical",
        site="demo-hotel",
    ),
    LiveCase(
        "manager_office",
        "Report for a manager, demo-office",
        "A different site, so figures copied from the first site's pattern would be caught.",
        audience="manager",
        site="demo-office",
    ),
)

INVESTIGATION_CASES: tuple[LiveCase, ...] = (
    LiveCase(
        "after_hours_spike",
        "Investigate event A1 at demo-clinic (a two hour spike overnight)",
        "A spike: hypotheses must be hedged, low or medium confidence, and grounded.",
        site="demo-clinic",
        anomaly_id="A1",
    ),
    LiveCase(
        "midday_dropout",
        "Investigate event A2 at demo-clinic (a one hour drop at midday)",
        "A drop: the causes differ from a spike and a metering gap is one of them.",
        site="demo-clinic",
        anomaly_id="A2",
    ),
)

SAVINGS_CASES: tuple[LiveCase, ...] = (
    LiveCase(
        "hotel_savings",
        "Savings advice for demo-hotel (night load, peak demand and load shifting levers)",
        "Three levers with an invented time of use tariff: ranges must be estimates.",
        site="demo-hotel",
    ),
    LiveCase(
        "office_savings",
        "Savings advice for demo-office (one lever, a flat tariff)",
        "One lever: the advisor must not invent others.",
        site="demo-office",
    ),
    LiveCase(
        "clinic_savings",
        "Savings advice for demo-clinic (night load and unusual spikes)",
        "A lever built on detected spikes, which are events to look into, not faults.",
        site="demo-clinic",
    ),
)

CASES_BY_AGENT: dict[str, tuple[LiveCase, ...]] = {
    "energy-analyst": LIVE_CASES,
    "report-writer": REPORT_CASES,
    "anomaly-investigator": INVESTIGATION_CASES,
    "savings-advisor": SAVINGS_CASES,
}


def _demo_data() -> DataSources:
    return DataSources(readings=synthetic_demo_source(), tariffs=demo_tariff_source())


async def evaluate(
    client: ModelClient,
    cases: Sequence[LiveCase] = LIVE_CASES,
    facts: HasVia | None = None,
    agent: str = "energy-analyst",
) -> list[CaseResult]:
    """Ask every case through the chosen agent, one at a time."""
    entry = AGENTS[agent]
    runner = entry.build(
        entry.spec, client, facts or entry.make_facts(entry.spec, "direct", _demo_data())
    )
    results = []
    for case in cases:
        # Each agent takes one kind of input. The others stay unset, which the registry checks.
        request = entry.make_request(
            RunInputs(
                site=case.site,
                question=case.question if entry.input_kind == "question" else None,
                audience=case.audience if entry.input_kind == "audience" else None,
                anomaly_id=case.anomaly_id if entry.input_kind == "anomaly" else None,
            )
        )
        results.append(CaseResult(case, await runner.run(request)))
    return results


def rejections(record: RunRecord) -> list[str]:
    """One line per rejected attempt: which attempt, what kind, and what was wrong."""
    lines = []
    for step in record.steps:
        problems = step.data.get("problems") if step.name == "validate_output" else None
        if problems:
            lines.append(
                f"attempt {step.data.get('attempt')} rejected ({step.data.get('kind')}): "
                + "; ".join(problems)
            )
    return lines


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
        for line in rejections(record):
            lines.append(f"    {line}")
        if show:
            lines.append(f"    question: {result.case.question}")
            if record.output is not None and "sections" in record.output:
                lines.append(f"    headline: {record.output['headline']}")
                for section in record.output["sections"]:
                    ids = ", ".join(section["fact_ids"])
                    lines.append(f"    section {section['heading']} [{ids}]: {section['body']}")
            elif record.output is not None and "actions" in record.output:
                lines.append(f"    summary: {record.output['summary']}")
                for action in record.output["actions"]:
                    ids = ", ".join(action["fact_ids"])
                    lines.append(f"    action [{ids}]: {action['title']}")
                    lines.append(f"        {action['explanation']}")
                lines.append(f"    caveat: {record.output['caveat']}")
            elif record.output is not None and "hypotheses" in record.output:
                lines.append(f"    summary: {record.output['summary']}")
                for item in record.output["hypotheses"]:
                    ids = ", ".join(item["fact_ids"])
                    lines.append(
                        f"    hypothesis [{ids}] ({item['confidence']}): {item['statement']}"
                    )
                    lines.append(f"        why {item['confidence']}: {item['confidence_reason']}")
                    lines.append(f"        check: {item['suggested_check']}")
            elif record.output is not None:
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


def save_records(path: Path, results: Sequence[CaseResult]) -> None:
    """Write every run record to a JSON file so results can be read or shared exactly."""
    payload = [
        {
            "case": r.case.name,
            "question": r.case.question,
            "purpose": r.case.purpose,
            "passed": r.passed,
            "record": r.record.model_dump(mode="json"),
        }
        for r in results
    ]
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


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
    save: Path | None = None,
    via: str = "direct",
    agent: str = "energy-analyst",
) -> int:
    chosen = client or build_client(provider, ProviderKeys(), model)
    entry = AGENTS[agent]
    facts = entry.make_facts(entry.spec, "mcp", _demo_data()) if via == "mcp" else None
    results = await evaluate(chosen, CASES_BY_AGENT[agent], facts=facts, agent=agent)
    print(format_report(results, show))
    if save is not None:
        save_records(save, results)
        print(f"Run records saved to {save}")
    return exit_code(results)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m energyops.agents.live_eval",
        description="Run the energy analyst's questions against a real model.",
    )
    parser.add_argument("provider", choices=("gemini", "nebius"))
    parser.add_argument("model", nargs="?", help="exact model name (default: the provider's)")
    parser.add_argument("--show", action="store_true", help="print each answer, not just results")
    parser.add_argument("--save", type=Path, metavar="FILE", help="write full run records as JSON")
    parser.add_argument(
        "--via",
        choices=("direct", "mcp"),
        default="direct",
        help="get the fact sheet by calling Python directly (default) or through the MCP server",
    )
    parser.add_argument(
        "--agent",
        choices=tuple(AGENTS),
        default="energy-analyst",
        help="which agent to evaluate (default: energy-analyst)",
    )
    args = parser.parse_args(argv)
    code = asyncio.run(
        run(args.provider, args.model, args.show, save=args.save, via=args.via, agent=args.agent)
    )
    raise SystemExit(code)


if __name__ == "__main__":
    main()
