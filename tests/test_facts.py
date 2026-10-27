from datetime import UTC, date, datetime, timedelta

import pytest

from energyops.calculations import EnergyDataError
from energyops.facts import build_fact_sheet
from energyops.synthetic import MeterReading, SiteProfile, generate_readings

MONDAY = datetime(2026, 3, 2, tzinfo=UTC)


def flat_week(kw: float = 10.0, days: int = 7) -> list[MeterReading]:
    return [
        MeterReading("m1", MONDAY + timedelta(minutes=15 * i), 15, kw) for i in range(96 * days)
    ]


def test_a_flat_week_gives_the_expected_figures() -> None:
    sheet = build_fact_sheet(flat_week(10.0))
    facts = {f.key: f for f in sheet.facts}
    assert facts["total_energy"].value == pytest.approx(10.0 * 24 * 7)
    assert facts["average_daily_energy"].value == pytest.approx(240.0)
    assert facts["average_weekday_energy"].value == pytest.approx(240.0)
    assert facts["average_weekend_energy"].value == pytest.approx(240.0)
    assert facts["peak_demand"].value == 10.0 and facts["lowest_demand"].value == 10.0
    assert facts["highest_day_energy"].when == date(2026, 3, 2)  # earliest day wins a tie
    assert sheet.warnings == ()


def test_ids_are_sequential_unique_and_findable() -> None:
    sheet = build_fact_sheet(flat_week())
    ids = [f.id for f in sheet.facts]
    assert ids == [f"F{i}" for i in range(1, len(ids) + 1)]
    assert sheet.by_id("F1") is sheet.facts[0]
    assert sheet.by_id("F999") is None


def test_weekend_is_lower_than_weekday_for_generated_data() -> None:
    readings = generate_readings(
        seed=4, meter_id="m1", start=MONDAY, days=14, profile=SiteProfile(noise_fraction=0)
    )
    facts = {f.key: f.value for f in build_fact_sheet(readings).facts}
    assert facts["average_weekend_energy"] < facts["average_weekday_energy"]
    assert facts["peak_demand"] > facts["lowest_demand"]


def test_display_uses_one_decimal_and_prompt_text_lists_every_fact() -> None:
    sheet = build_fact_sheet(flat_week(10.04))
    assert sheet.facts[0].display == "1686.7 kWh"
    text = sheet.to_prompt_text()
    assert all(f"{f.id} " in text for f in sheet.facts)
    assert "Meter m1" in text and "2026-03-02T00:00:00+00:00" in text


def test_missing_readings_are_flagged_and_left_out_of_daily_figures() -> None:
    readings = flat_week(10.0, days=3)
    del readings[100:110]  # punch a hole in day two
    sheet = build_fact_sheet(readings)
    assert any("2026-03-03" in w for w in sheet.warnings)
    assert {f.key for f in sheet.facts}.isdisjoint({"average_weekend_energy"})
    assert sheet.facts[0].value == pytest.approx(10.0 * 0.25 * len(readings))


def test_no_complete_day_gives_only_totals_and_demand_with_a_warning() -> None:
    sheet = build_fact_sheet(flat_week(10.0, days=1)[:40])
    assert {f.key for f in sheet.facts} == {"total_energy", "peak_demand", "lowest_demand"}
    assert any("No complete day" in w for w in sheet.warnings)


def test_untrustworthy_readings_are_rejected() -> None:
    with pytest.raises(EnergyDataError):
        build_fact_sheet([])
    bad = flat_week(days=1)
    bad.append(bad[0])
    with pytest.raises(EnergyDataError):
        build_fact_sheet(bad)


def test_unsorted_readings_still_give_the_right_period() -> None:
    sheet = build_fact_sheet(list(reversed(flat_week(days=2))))
    assert sheet.first_reading == MONDAY
    assert sheet.last_reading == MONDAY + timedelta(minutes=15 * (96 * 2 - 1))
