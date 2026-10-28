from datetime import UTC, datetime, timedelta

import pytest

from energyops.agents.grounding import check_text
from energyops.agents.sources import DEMO_SITES, synthetic_demo_source
from energyops.anomalies import detect_anomalies
from energyops.facts import Fact, FactSheet, build_fact_sheet


def test_demo_clinic_is_listed_and_the_other_sites_did_not_change() -> None:
    assert DEMO_SITES == ("demo-hotel", "demo-office", "demo-clinic")
    # Figures other tests and docs rely on: the first two sites keep their readings exactly.
    assert synthetic_demo_source()("demo-hotel")[0].power_kw == pytest.approx(20.935)
    assert detect_anomalies(synthetic_demo_source()("demo-hotel")).anomalies == ()
    assert detect_anomalies(synthetic_demo_source()("demo-office")).anomalies == ()


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_the_detector_finds_exactly_the_two_invented_clinic_events(seed: int) -> None:
    result = detect_anomalies(synthetic_demo_source(seed=seed)("demo-clinic"))
    first, second = result.anomalies
    assert (first.id, first.kind, first.intervals) == ("A1", "spike", 8)
    assert first.start == datetime(2026, 3, 4, 2, 0, tzinfo=UTC)
    assert first.end - first.start == timedelta(hours=2)
    assert (second.id, second.kind, second.intervals) == ("A2", "drop", 4)
    assert second.start == datetime(2026, 3, 12, 12, 0, tzinfo=UTC)


def test_the_clinic_still_gives_a_normal_fact_sheet() -> None:
    sheet = build_fact_sheet(synthetic_demo_source()("demo-clinic"))
    assert sheet.meter_id == "demo-clinic" and sheet.warnings == ()
    assert [f.key for f in sheet.facts][-1] == "days_covered"


def _sheet(*facts: Fact) -> FactSheet:
    stamp = datetime(2026, 3, 2, tzinfo=UTC)
    return FactSheet("m", stamp, stamp, facts, ())


def test_minutes_are_checked_only_when_the_sheet_has_a_minutes_fact() -> None:
    plain = _sheet(Fact("F1", "total_energy", "Total", 10.0, "kWh"))
    assert check_text("It lasted 90 minutes and then 5 min more.", plain) == []  # as before
    timed = _sheet(Fact("F1", "event_duration", "Duration", 120.0, "minutes"))
    assert check_text("It lasted 120 minutes.", timed) == []
    assert check_text("It lasted 2 minutes.", timed) == [
        "'2 minutes' does not match any computed minutes figure"
    ]
    assert check_text("About 45 min.", timed)  # the short form is checked too


def test_a_minutes_fact_is_shown_without_decimals() -> None:
    assert Fact("F1", "event_duration", "Duration", 120.0, "minutes").display == "120 minutes"
