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


# ------------------------------------------------------------------ demand and averages


def test_peak_and_lowest_demand_with_earliest_winning_ties() -> None:
    from energyops.calculations import lowest_demand, peak_demand

    readings = [
        reading(0, 5.0),
        reading(15, 9.0),
        reading(30, 2.0),
        reading(45, 9.0),
        reading(60, 2.0),
    ]
    peak, low = peak_demand(readings), lowest_demand(readings)
    assert (peak.power_kw, peak.timestamp) == (9.0, START + timedelta(minutes=15))
    assert (low.power_kw, low.timestamp) == (2.0, START + timedelta(minutes=30))


def test_peak_and_lowest_demand_reject_bad_input() -> None:
    from energyops.calculations import lowest_demand, peak_demand

    for fn in (peak_demand, lowest_demand):
        with pytest.raises(EnergyDataError):
            fn([])
        with pytest.raises(EnergyDataError):
            fn([reading(0, 1.0), reading(0, 2.0)])
        with pytest.raises(EnergyDataError):
            fn([reading(0, float("nan"))])


def test_average_daily_kwh_uses_complete_days_and_splits_weekdays_from_weekends() -> None:
    from energyops.calculations import DailyEnergy, average_daily_kwh

    days = [
        DailyEnergy(date(2026, 3, 2), 100.0, 96, True),  # Monday
        DailyEnergy(date(2026, 3, 3), 200.0, 96, True),  # Tuesday
        DailyEnergy(date(2026, 3, 7), 40.0, 96, True),  # Saturday
        DailyEnergy(date(2026, 3, 8), 999.0, 50, False),  # Sunday, partial: ignored
    ]
    assert average_daily_kwh(days) == pytest.approx((100 + 200 + 40) / 3)
    assert average_daily_kwh(days, weekend=False) == pytest.approx(150.0)
    assert average_daily_kwh(days, weekend=True) == pytest.approx(40.0)
    assert average_daily_kwh([DailyEnergy(date(2026, 3, 8), 1.0, 5, False)]) is None
    assert average_daily_kwh([]) is None
