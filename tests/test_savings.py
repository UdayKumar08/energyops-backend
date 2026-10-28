import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from energyops.agents.grounding import check_text
from energyops.agents.runs import RunStatus
from energyops.agents.savings_advisor import (
    SAVINGS_ADVISOR_SPEC,
    SavingsAdvice,
    SavingsAdvisor,
    SavingsRequest,
)
from energyops.agents.savings_advisor_evals import (
    CASES,
    SITE,
    good_savings_reply,
    run_scripted,
    savings_sheet_for,
)
from energyops.agents.savings_facts import DirectSavingsFacts, McpSavingsFacts
from energyops.agents.sources import DEMO_TARIFFS, demo_tariff_source, synthetic_demo_source
from energyops.agents.spec import load_prompt
from energyops.calculations import EnergyDataError
from energyops.facts import Fact, FactSheet
from energyops.mcp_servers.energy import create_server
from energyops.models.test_adapter import TestAdapter
from energyops.savings import MONTH_DAYS, NIGHT_REDUCTION, build_savings_sheet
from energyops.synthetic import MeterReading, generate_readings
from energyops.tariffs import Tariff, TariffBand, TariffError, cost_of

SOURCE = synthetic_demo_source()
TARIFFS = demo_tariff_source()
START = datetime(2026, 3, 2, tzinfo=UTC)  # a Monday


def reading(moment: datetime, kw: float) -> MeterReading:
    return MeterReading("m", moment, 15, kw)


# ------------------------------------------------------------------ tariffs


def test_a_reading_is_priced_by_the_band_it_falls_in() -> None:
    tariff = Tariff(
        "t",
        "GBP",
        0.10,
        bands=(
            TariffBand("Peak", 7, 21, 0.30, days="weekday"),
            TariffBand("Weekend day", 7, 21, 0.20, days="weekend"),
        ),
    )
    monday_noon, monday_night = START + timedelta(hours=12), START + timedelta(hours=2)
    saturday_noon = START + timedelta(days=5, hours=12)
    assert tariff.band_at(monday_noon) == ("Peak", 0.30)
    assert tariff.band_at(monday_night) == ("Standard", 0.10)
    assert tariff.band_at(saturday_noon) == ("Weekend day", 0.20)
    assert tariff.band_at(START + timedelta(hours=21)) == ("Standard", 0.10)  # end is exclusive


def test_cost_is_energy_times_the_price_of_each_interval_worked_out_by_hand() -> None:
    tariff = Tariff("t", "GBP", 0.10, bands=(TariffBand("Peak", 7, 21, 0.30, days="weekday"),))
    # 4 kW for 15 minutes is 1 kWh. Two intervals at night (0.10) and two at midday (0.30).
    readings = [
        reading(START + timedelta(hours=2), 4.0),
        reading(START + timedelta(hours=2, minutes=15), 8.0),
        reading(START + timedelta(hours=12), 4.0),
        reading(START + timedelta(hours=12, minutes=15), 12.0),
    ]
    result = cost_of(readings, tariff)
    assert result.energy_kwh == pytest.approx(1 + 2 + 1 + 3)
    assert result.by_band["Standard"] == pytest.approx((3.0, 0.30))
    assert result.by_band["Peak"] == pytest.approx((4.0, 1.20))
    assert result.total == pytest.approx(1.50)


@pytest.mark.parametrize(
    "build",
    [
        lambda: TariffBand("x", 5, 5, 0.1),
        lambda: TariffBand("x", 20, 26, 0.1),
        lambda: TariffBand("x", 22, 2, 0.1),
        lambda: TariffBand("", 1, 2, 0.1),
        lambda: TariffBand("x", 1, 2, -0.1),
        lambda: Tariff("t", "GBP", float("nan")),
        lambda: Tariff("t", "GBP", 0.1, demand_charge_per_kw_month=-1),
        lambda: Tariff("t", "JPY", 0.1),  # type: ignore[arg-type]
        lambda: Tariff("", "GBP", 0.1),
        lambda: Tariff(
            "t", "GBP", 0.1, bands=(TariffBand("a", 1, 5, 0.2), TariffBand("b", 4, 8, 0.3))
        ),
    ],
)
def test_unusable_tariffs_are_refused(build) -> None:
    with pytest.raises(TariffError):
        build()


def test_bands_for_different_kinds_of_day_may_share_hours() -> None:
    Tariff(
        "t",
        "GBP",
        0.1,
        bands=(
            TariffBand("a", 7, 9, 0.2, days="weekday"),
            TariffBand("b", 7, 9, 0.3, days="weekend"),
        ),
    )


# ------------------------------------------------------------------ the savings sheet


def facts_by_key(sheet: FactSheet) -> dict[str, Fact]:
    return {f.key: f for f in sheet.facts}


def test_the_hotel_sheet_has_three_levers_and_the_numbers_match_an_independent_calculation() -> (
    None
):
    readings = SOURCE("demo-hotel")
    tariff = DEMO_TARIFFS["demo-hotel"]
    sheet = build_savings_sheet(readings, tariff)
    facts = facts_by_key(sheet)

    total = sum(r.power_kw * 0.25 * tariff.band_at(r.timestamp)[1] for r in readings)
    assert facts["period_cost"].value == pytest.approx(total)
    assert facts["average_daily_cost"].value == pytest.approx(total / 14)
    assert facts["monthly_cost"].value == pytest.approx(total / 14 * MONTH_DAYS)
    assert facts["days_used"].value == 14 and facts["days_used"].unit == "days"

    night = [r for r in readings if r.timestamp.hour < 5]
    night_kwh = sum(r.power_kw * 0.25 for r in night)
    night_cost = sum(r.power_kw * 0.25 * tariff.band_at(r.timestamp)[1] for r in night)
    low, high = NIGHT_REDUCTION
    assert facts["night_saving_kwh_low"].value == pytest.approx(night_kwh * low / 100 / 14 * 30)
    assert facts["night_saving_cost_high"].value == pytest.approx(night_cost * high / 100 / 14 * 30)

    peak = max(r.power_kw for r in readings)
    assert facts["peak_demand"].value == peak
    assert facts["peak_saving_cost_low"].value == pytest.approx(peak * 0.05 * 6.5)
    assert facts["peak_saving_cost_high"].value == pytest.approx(peak * 0.10 * 6.5)

    dear_kwh = sum(r.power_kw * 0.25 for r in readings if tariff.band_at(r.timestamp)[0] == "Peak")
    assert facts["shift_saving_cost_low"].value == pytest.approx(dear_kwh * 0.15 * 0.05 / 14 * 30)
    assert "shift_saving_kwh_low" not in facts  # moving use saves money, not energy
    assert sheet.lever_count == 3


def test_ids_are_sequential_and_every_saving_is_labelled_as_an_assumption_or_estimate() -> None:
    sheet = savings_sheet_for(SOURCE, TARIFFS, "demo-hotel")
    assert [f.id for f in sheet.facts] == [f"F{i}" for i in range(1, len(sheet.facts) + 1)]
    for fact in sheet.facts:
        if "saving" in fact.key:
            assert "estimated" in fact.description
        if "assumed" in fact.key:
            assert "assumption" in fact.description
    assert (
        "ESTIMATE" in sheet.to_prompt_text() and "none of it is a promise" in sheet.to_prompt_text()
    )


def test_the_office_has_only_the_night_lever_because_its_tariff_is_flat() -> None:
    facts = facts_by_key(savings_sheet_for(SOURCE, TARIFFS, "demo-office"))
    assert not any(k.startswith(("peak_", "shift_", "spikes_")) for k in facts)
    assert "night_saving_cost_low" in facts


def test_the_clinic_has_a_spike_lever_and_the_night_lever_leaves_the_spike_out() -> None:
    clinic = SOURCE("demo-clinic")
    sheet = build_savings_sheet(clinic, DEMO_TARIFFS["demo-clinic"])
    facts = facts_by_key(sheet)
    assert facts["spike_excess"].value == pytest.approx(70.1, abs=0.05)
    assert facts["spikes_saving_kwh_low"].value == pytest.approx(70.1 * 0.5 / 14 * 30, rel=1e-3)
    # The night load is the median of ordinary night readings, so the spike does not raise it.
    assert facts["night_load"].value == pytest.approx(15.0, abs=0.5)
    # The spike lever covers energy the night lever does not: no energy is counted twice.
    spike_hours = {
        (datetime(2026, 3, 4, 2, 0, tzinfo=UTC) + timedelta(minutes=15 * i)) for i in range(8)
    }
    night_kwh = sum(
        r.power_kw * 0.25
        for r in clinic
        if r.timestamp.hour < 5 and r.timestamp not in spike_hours and r.timestamp.date().day != 12
    )
    assert facts["night_share"].value < night_kwh / sum(r.power_kw * 0.25 for r in clinic) * 100 + 1


def test_a_period_with_fewer_than_three_complete_days_is_refused() -> None:
    two_days = generate_readings(seed=1, meter_id="m", start=START, days=2)
    with pytest.raises(EnergyDataError, match="at least 3 complete days"):
        build_savings_sheet(two_days, TARIFFS("demo-office") or pytest.fail("no tariff"))


def test_incomplete_days_are_left_out_and_said_so() -> None:
    readings = list(generate_readings(seed=1, meter_id="m", start=START, days=5))
    del readings[100:130]  # a gap on the first day
    sheet = build_savings_sheet(readings, DEMO_TARIFFS["demo-office"])
    assert facts_by_key(sheet)["days_used"].value == 4
    assert any("2026-03-03" in w for w in sheet.warnings)


def test_a_site_with_no_lever_says_so_and_still_gives_costs() -> None:
    flat = [reading(START + timedelta(minutes=15 * i), 0.0) for i in range(96 * 3)]
    sheet = build_savings_sheet(flat, DEMO_TARIFFS["demo-office"])
    assert sheet.lever_count == 0
    assert any("No saving lever applies" in w for w in sheet.warnings)
    assert facts_by_key(sheet)["period_cost"].value == 0


# ------------------------------------------------------------------ money in the grounding check


def test_a_currency_amount_must_match_a_computed_amount_in_that_currency() -> None:
    sheet = savings_sheet_for(SOURCE, TARIFFS, "demo-hotel")
    monthly = facts_by_key(sheet)["monthly_cost"]
    assert check_text(f"About {monthly.display} a month.", sheet) == []
    assert check_text(f"About {monthly.display[:-1]}9 a month.", sheet)  # one penny out
    assert check_text("About $7,943.31 a month.", sheet)  # right number, wrong currency
    assert check_text("About 40 pounds.", sheet) and check_text("It cost 12 GBP.", sheet)


def test_an_energy_sheet_still_rejects_every_currency_amount() -> None:
    plain = FactSheet("m", START, START, (Fact("F1", "k", "d", 5.0, "kWh"),), ())
    assert check_text("It cost £5.00.", plain) == ["'£5.00': the fact sheet has no cost figures"]


def test_a_price_per_kwh_keeps_enough_decimals() -> None:
    assert Fact("F1", "p", "d", 0.2834, "£").display == "£0.2834"
    assert Fact("F1", "p", "d", 0.28, "£").display == "£0.28"
    assert Fact("F1", "p", "d", 1234.5, "£").display == "£1,234.50"


# ------------------------------------------------------------------ the agent


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_scripted_evaluation_cases(case) -> None:
    passed, record = asyncio.run(run_scripted(case))
    assert passed, (record.status, record.attempts, record.error_kind, record.error)


def advise(model, facts=None, site: str | None = SITE, spec=None):
    agent = SavingsAdvisor(
        spec or SAVINGS_ADVISOR_SPEC, model, facts or DirectSavingsFacts(SOURCE, TARIFFS)
    )
    return asyncio.run(agent.run(SavingsRequest(site=site)))


GOOD = good_savings_reply(savings_sheet_for(SOURCE, TARIFFS, SITE))


def test_a_good_run_returns_ranges_and_a_caveat_and_records_the_cost_sheet() -> None:
    record = advise(TestAdapter(GOOD))
    assert record.status is RunStatus.SUCCEEDED and record.attempts == 1
    advice = SavingsAdvice.model_validate(record.output)
    assert len(advice.actions) == 3 and "estimate" in advice.caveat.lower()
    sheet_step = next(s for s in record.steps if s.name == "fact_sheet")
    assert sheet_step.data["via"] == "direct"
    assert any(f["key"] == "monthly_cost" for f in sheet_step.data["facts"])
    assert record.model_provider == "test-adapter"


def test_the_mcp_path_gives_the_same_sheet_and_the_same_outcome() -> None:
    direct = advise(TestAdapter(GOOD))
    via_mcp = advise(
        TestAdapter(GOOD), facts=McpSavingsFacts(SAVINGS_ADVISOR_SPEC, create_server())
    )
    assert via_mcp.status is RunStatus.SUCCEEDED and via_mcp.output == direct.output
    names = [s.data["tool"] for s in via_mcp.steps if s.name == "tool_call"]
    assert names == ["get_savings_facts"]  # one named site: no list_sites call
    facts_direct = next(s for s in direct.steps if s.name == "fact_sheet").data["facts"]
    facts_mcp = next(s for s in via_mcp.steps if s.name == "fact_sheet").data["facts"]
    assert facts_direct == facts_mcp


def test_no_tariff_is_a_reported_failure_never_a_guessed_price() -> None:
    nothing = lambda site: None  # noqa: E731
    direct = advise(TestAdapter(GOOD), facts=DirectSavingsFacts(SOURCE, nothing))
    assert (direct.status, direct.error_kind) == (RunStatus.FAILED, "no_tariff")
    assert "no tariff" in (direct.error or "")
    server = create_server(source=SOURCE, tariffs=nothing)
    over_mcp = advise(TestAdapter(GOOD), facts=McpSavingsFacts(SAVINGS_ADVISOR_SPEC, server))
    assert (over_mcp.status, over_mcp.error_kind) == (RunStatus.FAILED, "no_tariff")
    assert not any(s.name == "model_call" for s in direct.steps + over_mcp.steps)


def test_a_custom_readings_source_without_tariffs_has_no_tariff_not_the_demo_one() -> None:
    server = create_server(source=SOURCE)
    record = advise(TestAdapter(GOOD), facts=McpSavingsFacts(SAVINGS_ADVISOR_SPEC, server))
    assert record.error_kind == "no_tariff"


def test_an_unknown_site_and_unusable_data_are_reported_like_for_the_other_agents() -> None:
    assert advise(TestAdapter(GOOD), site="nowhere").error_kind == "unknown_site"
    short = lambda site: generate_readings(seed=1, meter_id="m", start=START, days=2)  # noqa: E731
    record = advise(TestAdapter(GOOD), facts=DirectSavingsFacts(short, TARIFFS))
    assert record.error_kind == "unusable_data" and "3 complete days" in (record.error or "")


def test_a_sheet_with_no_lever_accepts_no_actions_and_refuses_invented_ones() -> None:
    flat = [reading(START + timedelta(minutes=15 * i), 0.0) for i in range(96 * 3)]
    facts = DirectSavingsFacts(lambda site: flat, lambda site: DEMO_TARIFFS["demo-office"])
    sheet = build_savings_sheet(flat, DEMO_TARIFFS["demo-office"])
    plain = good_savings_reply(sheet)
    assert advise(TestAdapter(plain), facts=facts).status is RunStatus.SUCCEEDED
    invented = (
        '{"summary": "x", "actions": [{"title": "t", "explanation": "An estimate.", '
        '"fact_ids": ["F1"]}], "caveat": "These are estimates, not guaranteed."}'
    )
    refused = advise(TestAdapter(invented), facts=facts)
    assert refused.status is RunStatus.FAILED and "no saving lever" in (refused.error or "")


def test_the_rejection_reasons_are_sent_back_to_the_model() -> None:
    told: list[str] = []

    class Spy(TestAdapter):
        async def complete(self, request):
            told.append(request.messages[-1].content)
            return await super().complete(request)

    promised = GOOD.replace("This is an estimate:", "This will save you money:", 1)
    record = advise(Spy(replies=(promised, GOOD)))
    assert record.status is RunStatus.SUCCEEDED and record.attempts == 2
    assert "'will save' promises or claims a saving" in told[1]


def test_the_spec_and_prompt_forbid_promises_and_the_tool_list_is_one_read_only_tool() -> None:
    spec = SAVINGS_ADVISOR_SPEC
    assert spec.status.value == "draft"
    assert spec.config.allowed_tools == ("list_sites", "get_savings_facts")
    prompt = load_prompt(spec)
    assert prompt.version == "v1" and len(prompt.sha256) == 64
    for needle in ("ESTIMATE", "Never promise", "both its low end and its high end", "carbon"):
        assert needle in prompt.text


def test_an_agent_not_allowed_the_savings_tool_cannot_use_it() -> None:
    narrow = SAVINGS_ADVISOR_SPEC.model_copy(
        update={
            "config": SAVINGS_ADVISOR_SPEC.config.model_copy(
                update={"allowed_tools": ("list_sites",)}
            )
        }
    )
    record = advise(TestAdapter(GOOD), facts=McpSavingsFacts(narrow, create_server()), spec=narrow)
    assert record.error_kind == "tool_error" and "get_savings_facts" in (record.error or "")
