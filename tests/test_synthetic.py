import random
from datetime import UTC, datetime, timedelta, timezone

import pytest

from energyops.synthetic import SiteProfile, generate_readings

START = datetime(2026, 3, 2, tzinfo=UTC)  # a Monday


def run(seed: int = 1, **kw):
    args = {"seed": seed, "meter_id": "m1", "start": START, "days": 3}
    return generate_readings(**{**args, **kw})


def test_same_seed_gives_identical_readings() -> None:
    assert run(7) == run(7)


def test_different_seed_gives_different_readings() -> None:
    assert run(7) != run(8)


def test_different_meters_with_the_same_seed_differ() -> None:
    assert run(7, meter_id="a") != run(7, meter_id="b")


def test_global_random_state_does_not_affect_or_get_touched() -> None:
    random.seed(123)
    before = random.getstate()
    first = run(7)
    assert random.getstate() == before
    random.seed(999)
    assert run(7) == first


def test_count_spacing_and_non_negative_power() -> None:
    readings = run(days=2, interval_minutes=30)
    assert len(readings) == 2 * 48
    gaps = {b.timestamp - a.timestamp for a, b in zip(readings, readings[1:], strict=False)}
    assert gaps == {timedelta(minutes=30)}
    assert all(r.power_kw >= 0 and r.interval_minutes == 30 for r in readings)
    assert readings[0].timestamp == START


def test_daytime_uses_more_power_than_night_and_weekends_less_than_weekdays() -> None:
    readings = generate_readings(
        seed=3, meter_id="m", start=START, days=7, profile=SiteProfile(noise_fraction=0)
    )
    by_time = {r.timestamp: r.power_kw for r in readings}
    monday_noon = by_time[START + timedelta(hours=13)]
    monday_night = by_time[START + timedelta(hours=2)]
    saturday_noon = by_time[START + timedelta(days=5, hours=13)]
    assert monday_noon > monday_night
    assert saturday_noon < monday_noon
    assert monday_night == pytest.approx(20.0)


def test_non_utc_start_is_converted_to_utc() -> None:
    plus_two = timezone(timedelta(hours=2))
    local_start = datetime(2026, 3, 2, 2, 0, tzinfo=plus_two)  # 00:00 UTC
    assert run(start=local_start) == run()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"days": 0},
        {"interval_minutes": 7},
        {"interval_minutes": 0},
        {"meter_id": ""},
        {"start": datetime(2026, 3, 2)},
        {"start": START + timedelta(minutes=7)},
        {"start": START + timedelta(seconds=1)},
    ],
)
def test_invalid_arguments_are_rejected(kwargs) -> None:
    with pytest.raises(ValueError):
        run(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [{"base_load_kw": -1}, {"peak_extra_kw": -1}, {"weekend_factor": 2}, {"noise_fraction": -0.1}],
)
def test_invalid_profile_is_rejected(kwargs) -> None:
    with pytest.raises(ValueError):
        SiteProfile(**kwargs)
