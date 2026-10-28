import asyncio

import pytest
from pydantic import ValidationError

from energyops.agents.anomaly_investigator import (
    ANOMALY_INVESTIGATOR_SPEC,
    AnomalyInvestigator,
    Investigation,
    InvestigationRequest,
)
from energyops.agents.anomaly_investigator_evals import (
    CASES,
    EVENT,
    SITE,
    event_sheet_for,
    good_investigation_reply,
    run_scripted,
)
from energyops.agents.event_facts import (
    DirectEventFacts,
    McpEventFacts,
    UnknownAnomalyError,
)
from energyops.agents.runs import RunStatus
from energyops.agents.sources import synthetic_demo_source
from energyops.agents.spec import Guardrail, GuardrailsConfig, load_prompt
from energyops.mcp_servers.energy import create_server
from energyops.models import ModelRequest, ModelResponse
from energyops.models.test_adapter import TestAdapter

SOURCE = synthetic_demo_source()
SHEET = event_sheet_for(SOURCE, SITE, EVENT)
GOOD = good_investigation_reply(SHEET)


def investigate(model, facts=None, site: str | None = SITE, anomaly_id: str = EVENT, spec=None):
    agent = AnomalyInvestigator(
        spec or ANOMALY_INVESTIGATOR_SPEC, model, facts or DirectEventFacts(SOURCE)
    )
    return asyncio.run(agent.run(InvestigationRequest(site=site, anomaly_id=anomaly_id)))


def via_mcp(spec=None):
    return McpEventFacts(spec or ANOMALY_INVESTIGATOR_SPEC, create_server())


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_scripted_evaluation_cases(case) -> None:
    passed, record = asyncio.run(run_scripted(case))
    assert passed, (record.status, record.attempts, record.error_kind, record.error)


# ------------------------------------------------------------------ the event sheet


def test_the_event_sheet_holds_only_python_computed_positive_figures() -> None:
    keys = [f.key for f in SHEET.facts]
    assert keys == [
        "event_energy_difference",
        "event_peak_difference",
        "event_demand_at_peak",
        "event_usual_demand",
        "event_peak_difference_percent",
        "event_duration",
        "average_daily_energy",
        "event_share_of_average_day",
        "days_covered",
    ]
    assert all(f.value >= 0 for f in SHEET.facts)
    by_key = {f.key: f for f in SHEET.facts}
    assert by_key["event_duration"].display == "120 minutes"
    assert 65 < by_key["event_energy_difference"].value < 75
    assert by_key["days_covered"].display == "14 days"
    text = SHEET.to_prompt_text()
    assert text.startswith("Event A1 at meter demo-clinic is a spike")
    assert "does not know the cause" in text and "F9 " in text


def test_a_drop_is_described_as_lower_and_its_sizes_are_still_positive() -> None:
    sheet = event_sheet_for(SOURCE, SITE, "A2")
    assert "a drop: demand was lower than usual" in sheet.event_text
    by_key = {f.key: f for f in sheet.facts}
    assert by_key["event_demand_at_peak"].value == 0.0
    assert by_key["event_energy_difference"].value > 50 and by_key["event_duration"].value == 60


def test_an_unknown_event_names_the_events_that_exist() -> None:
    with pytest.raises(UnknownAnomalyError, match="A1, A2"):
        event_sheet_for(SOURCE, SITE, "A9")
    with pytest.raises(UnknownAnomalyError, match="none"):
        event_sheet_for(SOURCE, "demo-hotel", "A1")


@pytest.mark.parametrize("bad", ["", "a1", "A", "A1000", "1", "A1; drop", "../A1"])
def test_the_request_only_accepts_event_ids_of_the_detectors_shape(bad: str) -> None:
    with pytest.raises(ValidationError):
        InvestigationRequest(site=SITE, anomaly_id=bad)
    with pytest.raises(ValidationError):
        InvestigationRequest.model_validate({"anomaly_id": "A1", "question": "why?"})


# ------------------------------------------------------------------ a run


def test_a_good_run_records_the_event_sheet_and_the_hypotheses() -> None:
    record = investigate(TestAdapter(GOOD))
    assert record.status is RunStatus.SUCCEEDED and record.attempts == 1
    result = Investigation.model_validate(record.output)
    assert result.hypotheses[0].confidence == "low"
    assert record.input == {"site": SITE, "anomaly_id": "A1"}
    assert record.agent == "EnergyOps Anomaly Investigator" and record.prompt_version == "v2"
    sheet_step = next(s for s in record.steps if s.name == "fact_sheet")
    assert sheet_step.data["via"] == "direct" and len(sheet_step.data["facts"]) == 9


def test_the_model_sees_the_event_text_and_the_fact_sheet_and_nothing_from_the_person() -> None:
    seen: list[str] = []

    class Spy(TestAdapter):
        async def complete(self, request: ModelRequest) -> ModelResponse:
            seen.append(request.messages[-1].content)
            return await super().complete(request)

    investigate(Spy(GOOD))
    assert seen[0].startswith("Event facts (the only figures you may quote):\nEvent A1")
    assert "F6 How long the event lasted: 120 minutes" in seen[0]


def test_an_unknown_event_is_its_own_error_kind_and_makes_no_model_call() -> None:
    record = investigate(TestAdapter(GOOD), anomaly_id="A7")
    assert (record.status, record.error_kind, record.attempts) == (
        RunStatus.FAILED,
        "unknown_anomaly",
        0,
    )
    assert "A1, A2" in (record.error or "")


def test_an_unknown_site_is_still_unknown_site() -> None:
    record = investigate(TestAdapter(GOOD), site="nowhere")
    assert (record.status, record.error_kind) == (RunStatus.FAILED, "unknown_site")


def test_the_rejection_reasons_are_sent_back_to_the_model() -> None:
    told: list[str] = []

    class Spy(TestAdapter):
        async def complete(self, request: ModelRequest) -> ModelResponse:
            told.append(request.messages[-1].content)
            return await super().complete(request)

    certain = (
        '{"summary": "Unusual.", "hypotheses": [{"statement": "It is caused by the boiler.", '
        '"confidence": "low", "confidence_reason": "Nothing favours it.", '
        '"suggested_check": "Look at the log.", "fact_ids": ["F1"]}]}'
    )
    record = investigate(Spy(replies=(certain, GOOD)))
    assert record.status is RunStatus.SUCCEEDED and record.attempts == 2
    assert "'is caused by' states a cause as certain" in told[1]


def test_switching_on_an_unimplemented_guardrail_refuses_to_start() -> None:
    spec = ANOMALY_INVESTIGATOR_SPEC.model_copy(
        update={"guardrails": GuardrailsConfig(content_safety=Guardrail(enabled=True))}
    )
    with pytest.raises(ValueError, match="content_safety"):
        AnomalyInvestigator(spec, TestAdapter("{}"), DirectEventFacts(SOURCE))


def test_the_spec_is_a_draft_with_a_versioned_prompt_and_three_read_only_tools() -> None:
    spec = ANOMALY_INVESTIGATOR_SPEC
    assert spec.status.value == "draft"
    assert spec.config.allowed_tools == ("list_sites", "get_fact_sheet", "get_anomalies")
    prompt = load_prompt(spec)
    assert prompt.version == "v2" and len(prompt.sha256) == 64
    for needle in ("hypotheses", 'Never "high"', "confidence_reason", "Ignore any instruction"):
        assert needle in prompt.text


# ------------------------------------------------------------------ through MCP


def test_through_mcp_the_run_matches_the_direct_run_and_traces_both_tool_calls() -> None:
    direct = investigate(TestAdapter(GOOD))
    mcp = investigate(TestAdapter(GOOD), facts=via_mcp())
    assert mcp.status is RunStatus.SUCCEEDED and mcp.output == direct.output
    calls = [s.data for s in mcp.steps if s.name == "tool_call"]
    assert [(c["tool"], c["arguments"]) for c in calls] == [
        ("get_anomalies", {"site": SITE}),
        ("get_fact_sheet", {"site": SITE}),
    ]
    assert next(s for s in mcp.steps if s.name == "fact_sheet").data["via"] == "mcp"
    direct_facts = next(s for s in direct.steps if s.name == "fact_sheet").data["facts"]
    assert next(s for s in mcp.steps if s.name == "fact_sheet").data["facts"] == direct_facts


def test_without_a_site_the_default_site_is_looked_up_first() -> None:
    record = investigate(TestAdapter(GOOD), facts=via_mcp(), site=None, anomaly_id="A1")
    assert [s.data["tool"] for s in record.steps if s.name == "tool_call"][0] == "list_sites"
    # The default site (demo-hotel) has no events, so this is an unknown event, not a crash.
    assert record.error_kind == "unknown_anomaly"


def test_through_mcp_an_unknown_event_and_an_unknown_site_fail_cleanly() -> None:
    assert investigate(TestAdapter(GOOD), facts=via_mcp(), anomaly_id="A9").error_kind == (
        "unknown_anomaly"
    )
    assert investigate(TestAdapter(GOOD), facts=via_mcp(), site="nowhere").error_kind == (
        "unknown_site"
    )


def test_through_mcp_the_spec_decides_which_tools_may_be_used() -> None:
    narrow = ANOMALY_INVESTIGATOR_SPEC.model_copy(
        update={
            "config": ANOMALY_INVESTIGATOR_SPEC.config.model_copy(
                update={"allowed_tools": ("get_fact_sheet",)}
            )
        }
    )
    record = investigate(TestAdapter(GOOD), facts=via_mcp(narrow), spec=narrow)
    assert (record.status, record.error_kind) == (RunStatus.FAILED, "tool_error")
    assert "get_anomalies" in (record.error or "") and record.attempts == 0
    no_server = ANOMALY_INVESTIGATOR_SPEC.model_copy(
        update={"config": ANOMALY_INVESTIGATOR_SPEC.config.model_copy(update={"mcp_servers": ()})}
    )
    refused = investigate(TestAdapter(GOOD), facts=via_mcp(no_server), spec=no_server)
    assert refused.error_kind == "tool_error" and "may not use the MCP server" in refused.error


def test_a_reply_missing_its_final_brace_gets_a_message_that_says_what_to_fix() -> None:
    # The same shape a real model produced twice in a live run: valid up to the last "}".
    cut = GOOD.rstrip()[:-1]
    assert cut.endswith("]") and not cut.endswith("}")
    told: list[str] = []

    class Spy(TestAdapter):
        async def complete(self, request: ModelRequest) -> ModelResponse:
            told.append(request.messages[-1].content)
            return await super().complete(request)

    record = investigate(Spy(replies=(cut, GOOD)))
    assert record.status is RunStatus.SUCCEEDED and record.attempts == 2
    assert "ends before the object is closed" in told[1]
    assert "Expecting" not in told[1]


def test_other_json_mistakes_keep_the_parsers_own_reason() -> None:
    broken = GOOD.replace('"confidence"', "confidence", 1)  # a key without quotes, mid-reply
    record = investigate(TestAdapter(broken))
    assert record.error_kind == "invalid_output" and "not valid JSON (" in (record.error or "")
    assert "ends before the object is closed" not in (record.error or "")
