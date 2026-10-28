"""The routing evaluation harness, run with the keyword router that stands in for a model."""

from __future__ import annotations

import json
from pathlib import Path

from energyops.agents.supervisor import ROUTES, SupervisorTools, keyword_route
from energyops.agents.supervisor_evals import (
    ROUTE_CASES,
    evaluate,
    exit_code,
    format_report,
    save_records,
)
from energyops.models.test_adapter import SimulatedProviderFailure, TestAdapter


class KeywordModel(TestAdapter):
    """The labelled test adapter, answering each question with the keyword router's reply."""

    async def complete(self, request):  # type: ignore[no-untyped-def]
        question = next(m.content for m in request.messages if m.role == "user")
        self._reply = keyword_route(question.split("\n", 1)[-1])
        return await super().complete(request)


def test_every_expected_route_exists_and_every_route_is_covered() -> None:
    assert {c.expected for c in ROUTE_CASES} == set(ROUTES)


async def test_the_harness_scores_a_keyword_router_and_shows_where_it_is_fooled() -> None:
    results = await evaluate(KeywordModel())
    wrong = {r.case.name for r in results if not r.passed}
    assert "order_to_route" in wrong  # a word matcher obeys the dictated route; a model should not
    assert {"savings", "forecast", "ticket", "investigate", "poem"} <= {
        r.case.name for r in results if r.passed
    }
    report = format_report(results, show=True)
    assert "WRONG   -> savings (expected decline)" in report
    assert "routes matched the expected one" in report
    assert exit_code(results) == 0


async def test_a_model_that_cannot_be_reached_is_not_counted_as_wrong(tmp_path: Path) -> None:
    results = await evaluate(TestAdapter(fail_with=SimulatedProviderFailure(503, "down")))
    assert all(r.infrastructure_failure for r in results) and exit_code(results) == 1
    assert "0 of 0 routes matched" in format_report(results)
    save_records(tmp_path / "out.json", results)
    saved = json.loads((tmp_path / "out.json").read_text())
    assert len(saved) == len(ROUTE_CASES) and saved[0]["chosen"] is None


def test_the_stand_in_tools_are_complete() -> None:
    assert SupervisorTools.__dataclass_fields__.keys() >= {"permissions", "run_agent", "events"}
