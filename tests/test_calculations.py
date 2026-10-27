from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from energyops.calculations import EnergyDataError, daily_energy_kwh, kw_to_kwh
from energyops.synthetic import MeterReading, SiteProfile, generate_readings

START = datetime(2026, 3, 2, tzinfo=UTC)


def reading(offset_minutes: int, kw: float, interval: int = 15, meter: str = "m") -> MeterReading:
    return MeterReading(meter, START + timedelta(minutes=offset_minutes), interval, kw)


@pytest.mark.parametrize(
    ("kw", "minutes", "kwh"),
    [(10.0, 60, 10.0), (10.0, 15, 2.5), (0.0, 15, 0.0), (4.0, 30, 2.0), (60.0, 1, 1.0)],
)
def test_kw_to_kwh(kw: float, minutes: int, kwh: float) -> None:
    assert kw_to_kwh(kw, minutes) == pytest.approx(kwh)


@pytest.mark.parametrize("minutes", [0, -15])
def test_kw_to_kwh_rejects_non_positive_interval(minutes: int) -> None:
    with pytest.raises(EnergyDataError):
        kw_to_kwh(1.0, minutes)


@pytest.mark.parametrize("kw", [float("nan"), float("inf")])
def test_kw_to_kwh_rejects_non_finite_power(kw: float) -> None:
    with pytest.raises(EnergyDataError):
        kw_to_kwh(kw, 15)


def test_constant_power_for_a_full_day_is_power_times_24_hours() -> None:
    readings = [reading(i * 15, 10.0) for i in range(96)]
    (day,) = daily_energy_kwh(readings)
    assert day.day == date(2026, 3, 2)
    assert day.energy_kwh == pytest.approx(240.0)
    assert day.intervals == 96 and day.complete


def test_midnight_boundary_reading_belongs_to_the_day_it_starts_in() -> None:
    readings = [reading(23 * 60 + 45, 8.0), reading(24 * 60, 4.0)]  # 23:45 and 00:00 next day
    first, second = daily_energy_kwh(readings)
    assert (first.day, first.energy_kwh) == (date(2026, 3, 2), pytest.approx(2.0))
    assert (second.day, second.energy_kwh) == (date(2026, 3, 3), pytest.approx(1.0))
    assert not first.complete and not second.complete


def test_missing_intervals_are_reported_as_incomplete() -> None:
    readings = [reading(i * 15, 10.0) for i in range(95)]
    (day,) = daily_energy_kwh(readings)
    assert day.intervals == 95 and not day.complete


def test_days_are_sorted_even_if_readings_are_not() -> None:
    days = daily_energy_kwh([reading(24 * 60, 1.0), reading(0, 1.0)])
    assert [d.day for d in days] == [date(2026, 3, 2), date(2026, 3, 3)]


def test_empty_input_gives_no_days() -> None:
    assert daily_energy_kwh([]) == []


def test_generated_data_totals_match_a_hand_sum() -> None:
    readings = generate_readings(
        seed=5, meter_id="m", start=START, days=2, profile=SiteProfile(noise_fraction=0)
    )
    days = daily_energy_kwh(readings)
    assert [d.complete for d in days] == [True, True]
    monday = sum(r.power_kw for r in readings[:96]) * 0.25
    assert days[0].energy_kwh == pytest.approx(monday)


@pytest.mark.parametrize(
    "bad",
    [
        [reading(0, 1.0), reading(0, 2.0)],  # duplicate timestamp
        [reading(0, 1.0, meter="a"), reading(15, 1.0, meter="b")],  # two meters
        [reading(0, 1.0, 15), reading(30, 1.0, 30)],  # two interval lengths
        [reading(7, 1.0)],  # off the interval boundary
        [reading(0, 1.0, interval=7)],  # interval does not divide a day
        [replace(reading(0, 1.0), timestamp=datetime(2026, 3, 2))],  # naive timestamp
        [
            replace(
                reading(0, 1.0), timestamp=datetime(2026, 3, 2, tzinfo=timezone(timedelta(hours=1)))
            )
        ],
    ],
)
def test_untrustworthy_readings_are_rejected(bad: list[MeterReading]) -> None:
    with pytest.raises(EnergyDataError):
        daily_energy_kwh(bad)
