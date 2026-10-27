import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from energyops.agents.energy_analyst import (
    ENERGY_ANALYST_SPEC,
    AnalystRequest,
    EnergyAnalyst,
)
from energyops.agents.energy_analyst_evals import CASES, good_reply, run_scripted
from energyops.agents.grounding import check_fact_ids, check_text
from energyops.agents.runs import RunStatus
from energyops.agents.sources import synthetic_demo_source
from energyops.agents.spec import (
    AgentConfig,
    AgentSpec,
    BlocklistGuardrail,
    Guardrail,
    GuardrailsConfig,
    ModelChoice,
    load_prompt,
)
from energyops.facts import build_fact_sheet
from energyops.models import Message, ModelClient, ModelRequest, ModelResponse, Usage
from energyops.models.test_adapter import SimulatedProviderFailure, TestAdapter

SOURCE = synthetic_demo_source()
SHEET = build_fact_sheet(SOURCE(None))


def agent(model: ModelClient, spec: AgentSpec = ENERGY_ANALYST_SPEC) -> EnergyAnalyst:
    return EnergyAnalyst(spec, model, SOURCE)


def ask(analyst: EnergyAnalyst, question: str = "Summarise this site.", site: str | None = None):
    return asyncio.run(analyst.run(AnalystRequest(question=question, site=site)))


def with_guardrails(**kw) -> AgentSpec:
    return ENERGY_ANALYST_SPEC.model_copy(update={"guardrails": GuardrailsConfig(**kw)})


# ------------------------------------------------------------------ evaluation cases


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_scripted_evaluation_cases(case) -> None:
    passed, record = asyncio.run(run_scripted(case))
    assert passed, (record.status, record.attempts, record.error_kind, record.error)


# ------------------------------------------------------------------ the run record


def test_a_good_run_records_output_usage_prompt_version_and_trace() -> None:
    record = ask(agent(TestAdapter(good_reply(SHEET))))
    assert record.status is RunStatus.SUCCEEDED and record.attempts == 1
    assert record.output is not None and record.output["findings"][0]["fact_ids"]
    assert (record.prompt_version, len(record.prompt_sha256)) == ("v2", 64)
    assert (record.model_provider, record.model_name) == ("test-adapter", "deterministic-v1")
    assert record.usage.estimated is True and record.usage.output_tokens > 0
    assert [s.name for s in record.steps] == [
        "check_input",
        "build_fact_sheet",
        "model_call",
        "validate_output",
    ]
    facts_step = record.steps[1].data["facts"]
    assert facts_step[0]["id"] == "F1" and "kWh" in facts_step[0]["display"]
    assert record.finished_at >= record.started_at
    json.loads(record.model_dump_json())  # a run record is plain, serialisable data


def test_a_retry_sends_the_rejection_reasons_back_to_the_model() -> None:
    seen: list[ModelRequest] = []

    class Spy(TestAdapter):
        async def complete(self, request: ModelRequest) -> ModelResponse:
            seen.append(request)
            return await super().complete(request)

    bad = '{"summary": "We used 999.9 kWh.", "findings": []}'
    ask(agent(Spy(replies=[bad, good_reply(SHEET)])))
    assert len(seen) == 2
    retry_messages = seen[1].messages
    assert retry_messages[-2].role == "assistant" and "999.9" in retry_messages[-2].content
    assert retry_messages[-1].role == "user" and "rejected" in retry_messages[-1].content
    assert "does not match any computed kWh figure" in retry_messages[-1].content


def test_the_prompt_sent_contains_the_fact_sheet_and_the_versioned_rules() -> None:
    seen: list[ModelRequest] = []

    class Spy(TestAdapter):
        async def complete(self, request: ModelRequest) -> ModelResponse:
            seen.append(request)
            return await super().complete(request)

    ask(agent(Spy(good_reply(SHEET))), question="What was the peak?")
    system, user = seen[0].messages
    assert "EnergyOps energy analyst" in system.content
    assert "What was the peak?" in user.content and "F1 " in user.content
    assert seen[0].temperature == 0.0


def test_a_reply_with_too_many_findings_is_told_exactly_what_is_wrong() -> None:
    # Found by the first live run: two real models sent valid JSON with nine findings, and the
    # old message wrongly said the reply was not JSON, so the retry could not fix it.
    seen: list[ModelRequest] = []

    class Spy(TestAdapter):
        async def complete(self, request: ModelRequest) -> ModelResponse:
            seen.append(request)
            return await super().complete(request)

    nine = json.dumps(
        {
            "summary": "Everything.",
            "findings": [{"text": f"Fact {i}.", "fact_ids": ["F1"]} for i in range(9)],
        }
    )
    record = ask(agent(Spy(replies=[nine, good_reply(SHEET)])))
    assert record.status is RunStatus.SUCCEEDED and record.attempts == 2
    told = seen[1].messages[-1].content
    assert "wrong shape" in told and "findings" in told and "at most 6" in told
    assert "not a single JSON object" not in told


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("Here you go!", "not a single JSON object"),
        ('{"summary": "x", "findings": [}', "not valid JSON"),
        ('{"summary": "", "findings": []}', "summary"),
        ('{"summary": "x", "extra": 1}', "extra"),
    ],
)
def test_each_kind_of_bad_reply_gets_its_own_explanation(reply: str, expected: str) -> None:
    record = ask(agent(TestAdapter(reply)))
    assert record.status is RunStatus.FAILED and record.error_kind == "invalid_output"
    assert expected in (record.error or "")


def test_prompt_versions_are_kept_and_the_new_one_asks_for_at_most_six_findings() -> None:
    old = ENERGY_ANALYST_SPEC.model_copy(
        update={
            "config": ENERGY_ANALYST_SPEC.config.model_copy(
                update={"prompt_file": "energy_analyst/v1.md", "prompt_version": "v1"}
            )
        }
    )
    assert load_prompt(old).sha256 != load_prompt(ENERGY_ANALYST_SPEC).sha256
    assert "at most 6 findings" in load_prompt(ENERGY_ANALYST_SPEC).text


# ---------------------------------------------------- failures are records, not crashes


def test_provider_failures_become_failed_runs_without_leaking_anything() -> None:
    failing = TestAdapter(fail_with=SimulatedProviderFailure(429, "slow down"))
    record = ask(agent(failing))
    assert record.status is RunStatus.FAILED
    assert record.error_kind == "model_error:ModelRateLimitError"
    assert record.attempts == 1 and record.output is None
    assert record.steps[-1].data["retryable"] is True


def test_a_slow_model_times_out() -> None:
    class Slow(TestAdapter):
        async def complete(self, request: ModelRequest) -> ModelResponse:
            await asyncio.sleep(5)
            return await super().complete(request)

    quick = ENERGY_ANALYST_SPEC.model_copy(
        update={"config": ENERGY_ANALYST_SPEC.config.model_copy(update={"timeout_seconds": 0.05})}
    )
    record = ask(agent(Slow(good_reply(SHEET)), quick))
    assert (record.status, record.error_kind) == (RunStatus.FAILED, "timeout")


def test_unknown_site_and_unusable_data_fail_cleanly() -> None:
    unknown = ask(agent(TestAdapter(good_reply(SHEET))), site="not-a-site")
    assert (unknown.status, unknown.error_kind) == (RunStatus.FAILED, "unknown_site")
    assert unknown.attempts == 0

    def empty(_site: str | None):
        return []

    record = asyncio.run(
        EnergyAnalyst(ENERGY_ANALYST_SPEC, TestAdapter("{}"), empty).run(
            AnalystRequest(question="hi")
        )
    )
    assert (record.status, record.error_kind) == (RunStatus.FAILED, "unusable_data")


def test_reply_wrapped_in_a_code_fence_is_accepted() -> None:
    fenced = "```json\n" + good_reply(SHEET) + "\n```"
    assert ask(agent(TestAdapter(fenced))).status is RunStatus.SUCCEEDED


def test_a_second_demo_site_works_and_has_its_own_figures() -> None:
    other = build_fact_sheet(SOURCE("demo-office"))
    record = ask(agent(TestAdapter(good_reply(other))), site="demo-office")
    assert record.status is RunStatus.SUCCEEDED


# ------------------------------------------------------------------ guardrails


def test_switching_on_an_unimplemented_guardrail_refuses_to_start() -> None:
    for name in ("content_safety", "prompt_shield"):
        with pytest.raises(ValueError, match=name):
            agent(TestAdapter("{}"), with_guardrails(**{name: Guardrail(enabled=True)}))


def test_blocklist_stops_a_blocked_question_before_any_model_call() -> None:
    spec = with_guardrails(blocklist=BlocklistGuardrail(enabled=True, terms=("secret",)))
    record = ask(agent(TestAdapter(good_reply(SHEET)), spec), question="Tell me the SECRET")
    assert (record.status, record.error_kind) == (RunStatus.FAILED, "blocklist_input")
    assert record.attempts == 0


def test_blocklist_also_checks_the_answer() -> None:
    spec = with_guardrails(blocklist=BlocklistGuardrail(enabled=True, terms=("site",)))
    record = ask(agent(TestAdapter(good_reply(SHEET)), spec), question="hello")
    assert (record.status, record.error_kind) == (RunStatus.FAILED, "blocklist_output")


def test_grounding_check_cannot_be_switched_off() -> None:
    with pytest.raises(ValidationError):
        GuardrailsConfig(grounding_check=False)  # type: ignore[arg-type]


# ------------------------------------------------------------------ grounding check on its own


def facts(key: str) -> str:
    return next(f for f in SHEET.facts if f.key == key).display


def test_figures_that_match_the_fact_sheet_pass() -> None:
    peak = next(f for f in SHEET.facts if f.key == "peak_demand")
    when = peak.when.isoformat()  # type: ignore[union-attr]
    text = f"Peak was {facts('peak_demand')} at {when} (F{peak.id[1:]}), around 13:00 on day 3."
    assert check_text(text, SHEET) == []


def test_numbers_without_units_dates_times_and_ids_are_not_treated_as_figures() -> None:
    assert (
        check_text("Over 14 days, on 2026-03-02T00:00:00+00:00 and at 13:15, see F3.", SHEET) == []
    )


@pytest.mark.parametrize(
    "text",
    [
        "Usage was 999.9 kWh.",
        "Peak of 3.1 kW.",
        "That is 45 % lower.",
        "We saved £50.",
        "Cost $12.",
        "Total was 1.5 MWh.",
        "About 5 W standby.",
    ],
)
def test_invented_figures_are_flagged(text: str) -> None:
    assert check_text(text, SHEET)


def test_rounding_is_compared_at_the_precision_the_writer_used() -> None:
    total = SHEET.facts[0].value
    assert check_text(f"{total:,.1f} kWh", SHEET) == []  # thousands separator, one decimal
    assert check_text(f"{round(total):,} kWh", SHEET) == []  # whole number
    assert check_text(f"{total + 1:.1f} kWh", SHEET) != []  # a different figure
    assert check_text(f"{total:.1f} kW", SHEET) != []  # right number, wrong unit


def test_percentage_sign_is_ignored() -> None:
    change = next(f for f in SHEET.facts if f.key == "weekend_vs_weekday_change")
    assert change.value < 0
    assert check_text(f"Weekends are {abs(change.value):.1f}% lower.", SHEET) == []
    assert check_text(f"Weekends are {change.value:.1f}% lower.", SHEET) == []


def test_fact_ids_must_exist() -> None:
    assert check_fact_ids(["F1", "F2"], SHEET) == []
    assert check_fact_ids(["F1", "F99"], SHEET) == ["'F99' is not a fact id in the fact sheet"]


# ------------------------------------------------------------------ the spec


def test_the_analyst_spec_matches_the_registry_style_fields() -> None:
    spec = ENERGY_ANALYST_SPEC
    assert (
        spec.status.value == "draft"
    )  # not active until it has been evaluated against a real model
    assert spec.config.max_iterations == 2 and spec.config.temperature == 0
    assert "peak-demand" in spec.capabilities
    prompt = load_prompt(spec)
    assert prompt.version == "v2" and "fact sheet" in prompt.text


@pytest.mark.parametrize(
    "bad",
    [
        {"capabilities": ("Not A Tag",)},
        {"capabilities": ("a", "a")},
        {"capabilities": ()},
        {"name": "x"},
        {"description": ""},
    ],
)
def test_invalid_specs_are_rejected(bad) -> None:
    data = ENERGY_ANALYST_SPEC.model_dump() | bad
    with pytest.raises(ValidationError):
        AgentSpec.model_validate(data)


@pytest.mark.parametrize(
    "file", ["../secrets.md", "energy_analyst/v1.txt", "/etc/passwd", "a/b/c.md"]
)
def test_prompt_file_paths_cannot_escape_the_prompts_folder(file: str) -> None:
    with pytest.raises(ValidationError):
        AgentConfig(
            model=ModelChoice(provider="p", name="m"), prompt_file=file, prompt_version="v1"
        )


def test_message_and_usage_types_are_reused_not_redefined() -> None:
    assert Message("user", "x").role == "user" and Usage(1, 2).total_tokens == 3
    assert datetime.now(UTC) - timedelta(seconds=5) < datetime.now(UTC)
