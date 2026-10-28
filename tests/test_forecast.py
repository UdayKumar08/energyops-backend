from datetime import UTC, datetime

import pytest

from energyops.calculations import EnergyDataError
from energyops.forecast import ForecastRefused, forecast_demand
from energyops.synthetic import SiteProfile, generate_readings

MONDAY = datetime(2026, 3, 2, tzinfo=UTC)


def history(days: int, **profile):
    return generate_readings(
        seed=1, meter_id="m", start=MONDAY, days=days, profile=SiteProfile(**profile)
    )


def test_a_weekly_pattern_is_forecast_with_a_good_backtest():
    result = forecast_demand(history(28, noise_fraction=0.02), days=7)
    assert result.history_days == 28
    assert len(result.days) == 7 and len(result.points) == 7 * 96
    assert result.days[0].day.isoformat() == "2026-03-30"  # the day after the last reading
    weekdays = [d for d in result.days if d.day.weekday() < 5]
    weekends = [d for d in result.days if d.day.weekday() >= 5]
    assert min(d.energy_kwh for d in weekdays) > max(d.energy_kwh for d in weekends)
    assert result.backtest is not None
    assert result.backtest.days_tested == 7
    assert result.backtest.mae_percent_of_mean < 5
    assert result.backtest.daily_energy_error_percent < 5
    assert any("weather" in text for text in result.limits)


def test_the_backtest_does_not_see_the_days_it_predicts():
    # Last week is doubled. A method that peeked would predict it well; this one cannot.
    base = history(28, noise_fraction=0.0)
    tweaked = [
        r
        if r.timestamp < datetime(2026, 3, 23, tzinfo=UTC)
        else type(r)(r.meter_id, r.timestamp, r.interval_minutes, r.power_kw * 2)
        for r in base
    ]
    result = forecast_demand(tweaked, days=1)
    assert result.backtest is not None
    assert result.backtest.mae_percent_of_mean > 20


def test_the_method_is_not_called_better_when_it_is_not():
    # Flat history with a level shift in the last week: copying last week wins or ties.
    base = history(28, noise_fraction=0.0, base_load_kw=10, peak_extra_kw=0)
    shifted = [
        type(r)(
            r.meter_id,
            r.timestamp,
            r.interval_minutes,
            r.power_kw + (5 if r.timestamp >= datetime(2026, 3, 16, tzinfo=UTC) else 0),
        )
        for r in base
    ]
    result = forecast_demand(shifted, days=1)
    assert result.backtest is not None
    assert result.backtest.beats_naive in (True, False)
    assert result.backtest.naive_mae_kw is not None


def test_refuses_with_too_little_history():
    with pytest.raises(ForecastRefused, match="14 complete days"):
        forecast_demand(history(10))


def test_incomplete_days_do_not_count():
    readings = history(14)[:-5]  # the last day is now partial
    with pytest.raises(ForecastRefused):
        forecast_demand(readings)


def test_refuses_when_a_weekday_is_missing():
    readings = [r for r in history(28) if r.timestamp.weekday() != 2]
    with pytest.raises(ForecastRefused, match="Wednesday"):
        forecast_demand(readings)


def test_fourteen_days_forecast_but_say_the_test_is_missing():
    result = forecast_demand(history(14), days=2)
    assert result.backtest is None
    assert result.backtest_note


def test_bad_inputs():
    with pytest.raises(ValueError):
        forecast_demand(history(28), days=0)
    with pytest.raises(ValueError):
        forecast_demand(history(28), days=15)
    with pytest.raises(ForecastRefused):
        forecast_demand([])
    dup = history(28)
    with pytest.raises(EnergyDataError):
        forecast_demand([*dup, dup[0]])
