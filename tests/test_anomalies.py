from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from energyops.agents.sources import DEMO_SITES, synthetic_demo_source
from energyops.anomalies import DetectorSettings, detect_anomalies
from energyops.calculations import EnergyDataError
from energyops.synthetic import MeterReading, generate_readings

MONDAY = datetime(2026, 3, 2, tzinfo=UTC)


def demo(site: str = "demo-hotel", days: int = 14) -> list[MeterReading]:
    return list(generate_readings(seed=1, meter_id=site, start=MONDAY, days=days))


def edit(readings, first: datetime, count: int, change) -> list[MeterReading]:
    """Return a copy where `count` consecutive readings from `first` have been changed."""
    out = list(readings)
    for i, reading in enumerate(out):
        if first <= reading.timestamp < first + timedelta(minutes=15 * count):
            out[i] = replace(reading, power_kw=round(change(reading.power_kw), 3))
    return out


def test_ordinary_noisy_synthetic_data_has_no_anomalies_across_many_seeds() -> None:
    # A measured result, not a guarantee for real buildings: 600 clean runs (300 seeds, two
    # meters) gave no false events when this was tuned. 100 seeds per demo site keep it checked.
    for site in DEMO_SITES:
        for seed in range(100):
            readings = generate_readings(seed=seed, meter_id=site, start=MONDAY, days=14)
            assert detect_anomalies(readings).anomalies == (), (site, seed)


def test_a_sustained_change_of_forty_percent_is_found_across_many_seeds() -> None:
    start = datetime(2026, 3, 4, 11, 0, tzinfo=UTC)
    for seed in range(40):
        base = generate_readings(seed=seed, meter_id="m", start=MONDAY, days=14)
        readings = edit(base, start, 8, lambda kw: kw * 1.4)
        found = detect_anomalies(readings).anomalies
        assert any(start <= a.start < start + timedelta(hours=2) for a in found), seed


def test_the_synthetic_demo_source_is_clean_and_fully_judged() -> None:
    result = detect_anomalies(synthetic_demo_source()(None))
    assert result.anomalies == () and result.unjudged_readings == 0
    assert result.judged_readings == 14 * 96


def test_an_injected_overnight_spike_is_found_as_one_event_with_the_right_numbers() -> None:
    start = datetime(2026, 3, 4, 2, 0, tzinfo=UTC)  # a Wednesday night
    readings = edit(demo(), start, 8, lambda kw: kw + 40.0)
    (found,) = detect_anomalies(readings).anomalies
    assert found.id == "A1" and found.kind == "spike" and found.intervals == 8
    assert found.start == start and found.end == start + timedelta(hours=2)
    assert 35 < found.peak_deviation_kw < 45
    assert found.observed_kw_at_peak - found.baseline_kw_at_peak == pytest.approx(
        found.peak_deviation_kw
    )
    # 8 intervals of about +40 kW for 15 minutes each is about 80 kWh.
    assert found.energy_difference_kwh == pytest.approx(80, abs=8)


def test_a_daytime_dropout_is_found_as_a_drop() -> None:
    start = datetime(2026, 3, 5, 12, 0, tzinfo=UTC)  # a Thursday, around peak load
    readings = edit(demo(), start, 4, lambda kw: 0.0)
    (found,) = detect_anomalies(readings).anomalies
    assert found.kind == "drop" and found.intervals == 4
    assert found.peak_deviation_kw < -60 and found.energy_difference_kwh < -50


def test_a_weekend_spike_is_judged_against_other_weekend_days_not_weekdays() -> None:
    # Saturday 12:00 at weekday-like load would be a normal weekday but is a spike on a weekend.
    sat = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)
    weekday_like = lambda kw: kw * 2.0  # noqa: E731
    (found,) = detect_anomalies(edit(demo(), sat, 4, weekday_like)).anomalies
    assert found.kind == "spike" and found.start == sat


def test_a_spike_followed_by_a_drop_is_two_events_not_one() -> None:
    first = datetime(2026, 3, 4, 2, 0, tzinfo=UTC)
    readings = edit(demo(), first, 2, lambda kw: kw + 40.0)
    readings = edit(readings, first + timedelta(minutes=30), 2, lambda kw: 0.0)
    result = detect_anomalies(readings)
    assert [(a.kind, a.intervals) for a in result.anomalies] == [("spike", 2), ("drop", 2)]
    assert [a.id for a in result.anomalies] == ["A1", "A2"]


def test_separate_spikes_are_separate_events_in_time_order() -> None:
    readings = edit(demo(), datetime(2026, 3, 9, 3, 0, tzinfo=UTC), 1, lambda kw: kw + 40.0)
    readings = edit(readings, datetime(2026, 3, 3, 3, 0, tzinfo=UTC), 1, lambda kw: kw + 40.0)
    found = detect_anomalies(readings).anomalies
    assert [a.start.day for a in found] == [3, 9] and [a.id for a in found] == ["A1", "A2"]


def test_a_small_wobble_is_not_flagged() -> None:
    readings = edit(demo(), datetime(2026, 3, 4, 2, 0, tzinfo=UTC), 4, lambda kw: kw + 1.0)
    assert detect_anomalies(readings).anomalies == ()


def test_missing_readings_do_not_break_detection_and_a_spike_is_still_found() -> None:
    start = datetime(2026, 3, 4, 2, 0, tzinfo=UTC)
    readings = edit(demo(), start, 4, lambda kw: kw + 40.0)
    del readings[500:520]  # a gap on another day
    result = detect_anomalies(readings)
    assert [a.start for a in result.anomalies] == [start]


def test_too_little_history_is_reported_as_unjudged_not_guessed() -> None:
    result = detect_anomalies(demo(days=3))  # Mon to Wed: only two other weekdays per slot
    assert result.anomalies == () and result.judged_readings == 0
    assert result.unjudged_readings == 3 * 96


def test_the_weekend_needs_enough_weekend_days_so_a_short_history_leaves_it_unjudged() -> None:
    result = detect_anomalies(demo(days=14))  # 4 weekend days: each has 3 others, just enough
    assert result.unjudged_readings == 0
    weeklong = detect_anomalies(demo(days=7))  # 2 weekend days: only 1 other
    assert weeklong.unjudged_readings == 2 * 96


def test_the_result_is_deterministic_and_does_not_change_its_input() -> None:
    readings = edit(demo(), datetime(2026, 3, 4, 2, 0, tzinfo=UTC), 8, lambda kw: kw + 40.0)
    snapshot = list(readings)
    assert detect_anomalies(readings) == detect_anomalies(list(reversed(readings)))
    assert readings == snapshot


def test_stricter_settings_find_less_and_looser_find_more() -> None:
    readings = edit(demo(), datetime(2026, 3, 4, 2, 0, tzinfo=UTC), 4, lambda kw: kw + 8.0)
    assert len(detect_anomalies(readings).anomalies) == 1
    strict = DetectorSettings(min_kw=50.0)
    assert detect_anomalies(readings, strict).anomalies == ()


@pytest.mark.parametrize(
    "bad", [{"min_comparable_readings": 2}, {"sigmas": 0}, {"min_relative": -0.1}, {"min_kw": -1}]
)
def test_unsafe_settings_are_refused(bad) -> None:
    with pytest.raises(ValueError):
        DetectorSettings(**bad)


def test_untrustworthy_readings_are_refused_like_everywhere_else() -> None:
    with pytest.raises(EnergyDataError):
        detect_anomalies([])
    readings = demo(days=1)
    with pytest.raises(EnergyDataError):
        detect_anomalies(readings + [readings[0]])  # duplicate timestamp
