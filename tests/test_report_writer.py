import asyncio

import pytest

from energyops.agents.fact_sources import DirectFactSheets
from energyops.agents.report_writer import (
    REPORT_WRITER_SPEC,
    ReportRequest,
    ReportWriter,
    SiteReport,
)
from energyops.agents.report_writer_evals import (
    CASES,
    gappy_source,
    good_report_reply,
    run_scripted,
)
from energyops.agents.runs import RunStatus
from energyops.agents.sources import synthetic_demo_source
from energyops.agents.spec import Guardrail, GuardrailsConfig, load_prompt
from energyops.facts import build_fact_sheet
from energyops.models import ModelRequest, ModelResponse
from energyops.models.test_adapter import TestAdapter

SOURCE = synthetic_demo_source()
SHEET = build_fact_sheet(SOURCE(None))


def writer(model: TestAdapter, source=SOURCE, spec=REPORT_WRITER_SPEC) -> ReportWriter:
    return ReportWriter(spec, model, DirectFactSheets(source))


def write(model: TestAdapter, request: ReportRequest | None = None, source=SOURCE):
    return asyncio.run(writer(model, source).run(request or ReportRequest()))


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_scripted_evaluation_cases(case) -> None:
    passed, record = asyncio.run(run_scripted(case))
    assert passed, (record.status, record.attempts, record.error_kind, record.error)


def test_a_good_report_is_returned_with_its_trace() -> None:
    record = write(TestAdapter(good_report_reply(SHEET)))
    assert record.status is RunStatus.SUCCEEDED and record.attempts == 1
    report = SiteReport.model_validate(record.output)
    assert [s.heading for s in report.sections] == ["Consumption", "Demand"]
    assert record.agent == "EnergyOps Report Writer" and record.prompt_version == "v1"
    assert [s.name for s in record.steps] == [
        "check_input",
        "fact_sheet",
        "model_call",
        "validate_output",
    ]
    assert record.input == {"site": None, "audience": "manager"}


def test_the_audience_and_the_fact_sheet_reach_the_model_but_no_question_exists() -> None:
    seen: list[str] = []

    class Spy(TestAdapter):
        async def complete(self, request: ModelRequest) -> ModelResponse:
            seen.append(request.messages[-1].content)
            return await super().complete(request)

    write(Spy(good_report_reply(SHEET)), ReportRequest(audience="technical"))
    assert seen[0].startswith("Audience: technical")
    assert "F1 " in seen[0] and "Fact sheet (the only figures you may quote)" in seen[0]
    with pytest.raises(ValueError):
        ReportRequest.model_validate({"audience": "manager", "question": "hi"})
    with pytest.raises(ValueError):
        ReportRequest.model_validate({"audience": "the press"})


def test_data_with_gaps_needs_a_data_quality_section_and_clean_data_must_not_have_one() -> None:
    gappy = build_fact_sheet(gappy_source()(None))
    assert gappy.warnings
    ok = write(TestAdapter(good_report_reply(gappy)), source=gappy_source())
    assert ok.status is RunStatus.SUCCEEDED
    assert "Data quality" in [s["heading"] for s in ok.output["sections"]]  # type: ignore[index]
    assert not SHEET.warnings


def test_the_rejection_reasons_are_sent_back_to_the_model() -> None:
    told: list[str] = []

    class Spy(TestAdapter):
        async def complete(self, request: ModelRequest) -> ModelResponse:
            told.append(request.messages[-1].content)
            return await super().complete(request)

    bad = (
        '{"headline": "x", "sections": [{"heading": "Demand", "body": "y", "fact_ids": ["F1"]},'
        ' {"heading": "Consumption", "body": "z", "fact_ids": ["F1"]}]}'
    )
    record = write(Spy(replies=(bad, good_report_reply(SHEET))))
    assert record.status is RunStatus.SUCCEEDED and record.attempts == 2
    assert "sections must be in this order" in told[1]


def test_a_blocked_term_in_the_report_is_refused() -> None:
    spec = REPORT_WRITER_SPEC.model_copy(
        update={
            "guardrails": GuardrailsConfig(
                blocklist={"enabled": True, "terms": ("peak demand",)}  # type: ignore[arg-type]
            )
        }
    )
    record = asyncio.run(
        ReportWriter(spec, TestAdapter(good_report_reply(SHEET)), DirectFactSheets(SOURCE)).run(
            ReportRequest()
        )
    )
    assert (record.status, record.error_kind) == (RunStatus.FAILED, "blocklist_output")


def test_switching_on_an_unimplemented_guardrail_refuses_to_start() -> None:
    spec = REPORT_WRITER_SPEC.model_copy(
        update={"guardrails": GuardrailsConfig(prompt_shield=Guardrail(enabled=True))}
    )
    with pytest.raises(ValueError, match="prompt_shield"):
        writer(TestAdapter("{}"), spec=spec)


def test_unknown_site_fails_cleanly_without_a_model_call() -> None:
    record = write(TestAdapter(good_report_reply(SHEET)), ReportRequest(site="nowhere"))
    assert (record.status, record.error_kind, record.attempts) == (
        RunStatus.FAILED,
        "unknown_site",
        0,
    )


def test_the_prompt_is_versioned_and_fingerprinted_and_states_the_core_rules() -> None:
    prompt = load_prompt(REPORT_WRITER_SPEC)
    assert prompt.version == "v1" and len(prompt.sha256) == 64
    for needle in ("Consumption", "Data quality", "fact_ids", "Ignore any instruction"):
        assert needle in prompt.text


def test_the_spec_is_a_draft_that_only_reaches_the_read_only_energy_tools() -> None:
    config = REPORT_WRITER_SPEC.config
    assert REPORT_WRITER_SPEC.status.value == "draft"
    assert config.mcp_servers == ("energyops-energy",)
    assert config.allowed_tools == ("list_sites", "get_fact_sheet")
