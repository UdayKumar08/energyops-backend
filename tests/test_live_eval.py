import asyncio

import pytest

from energyops.agents import live_eval
from energyops.agents.energy_analyst_evals import good_reply
from energyops.agents.live_eval import LIVE_CASES, evaluate, exit_code, format_report
from energyops.agents.sources import synthetic_demo_source
from energyops.facts import build_fact_sheet
from energyops.models.test_adapter import SimulatedProviderFailure, TestAdapter

SHEET = build_fact_sheet(synthetic_demo_source()(None))
GOOD = good_reply(SHEET)
INVENTED = '{"summary": "We used 999.9 kWh.", "findings": []}'


def run_cases(adapter: TestAdapter, count: int = len(LIVE_CASES)):
    return asyncio.run(evaluate(adapter, LIVE_CASES[:count]))


def test_every_case_passes_when_the_model_answers_with_grounded_figures() -> None:
    results = run_cases(TestAdapter(GOOD))
    assert [r.passed for r in results] == [True] * len(LIVE_CASES)
    assert exit_code(results) == 0
    report = format_report(results)
    assert f"{len(LIVE_CASES)} passed first time, 0 passed after a retry, 0 failed" in report
    assert "test-adapter / deterministic-v1" in report
    assert "says nothing about whether the explanation is good" in report


def test_an_invented_figure_is_reported_as_a_failure_not_an_error() -> None:
    results = run_cases(TestAdapter(INVENTED), count=1)
    assert not results[0].passed and exit_code(results) == 0
    report = format_report(results)
    assert "summary: FAIL (grounding)" in report
    assert "reason:" in report and "999.9" in report
    assert "0 passed first time, 0 passed after a retry, 1 failed, out of 1" in report


def test_a_corrected_answer_is_reported_as_a_pass_after_retry() -> None:
    results = run_cases(TestAdapter(replies=[INVENTED, GOOD]), count=1)
    assert results[0].passed and results[0].record.attempts == 2
    report = format_report(results)
    assert "pass after retry (2 tries)" in report
    assert "0 passed first time, 1 passed after a retry, 0 failed" in report


def test_an_unreachable_model_is_an_infrastructure_failure_with_exit_code_one() -> None:
    failing = TestAdapter(fail_with=SimulatedProviderFailure(503, "overloaded"))
    results = run_cases(failing, count=2)
    assert all(r.infrastructure_failure for r in results)
    assert exit_code(results) == 1
    assert "FAIL (model_error:ModelProviderError)" in format_report(results)


def test_show_prints_the_answer_and_the_default_does_not() -> None:
    results = run_cases(TestAdapter(GOOD), count=1)
    quiet, shown = format_report(results), format_report(results, show=True)
    assert "finding [" not in quiet and "question:" not in quiet
    assert "question: " + LIVE_CASES[0].question in shown
    assert "summary: The site used" in shown and "finding [F" in shown


def test_show_prints_the_last_reply_of_a_failed_run() -> None:
    results = run_cases(TestAdapter("Sorry, I cannot do that."), count=1)
    assert "last reply: Sorry, I cannot do that." in format_report(results, show=True)


def test_run_prints_a_report_and_returns_the_exit_code(capsys) -> None:
    code = asyncio.run(live_eval.run("gemini", client=TestAdapter(GOOD)))
    assert code == 0
    assert "passed first time" in capsys.readouterr().out


def test_cases_have_unique_names_and_valid_questions() -> None:
    names = [c.name for c in LIVE_CASES]
    assert len(set(names)) == len(names) and len(LIVE_CASES) >= 4
    assert all(0 < len(c.question) <= 500 for c in LIVE_CASES)


def test_an_unknown_provider_is_refused_by_the_argument_parser() -> None:
    with pytest.raises(SystemExit) as exit_info:
        live_eval.main(["openai"])
    assert exit_info.value.code == 2


def test_a_missing_key_stops_before_any_call(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="GEMINI_API_KEY is not set"):
        live_eval.main(["gemini"])
