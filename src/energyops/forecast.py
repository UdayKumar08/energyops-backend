"""A plain seasonal demand forecast, with an honest test of how well it would have done.

The method is deliberately simple so that it can be explained and checked. For each day of the
week and each time of day it takes the median power seen on complete days of the same weekday,
and repeats that pattern forward. It knows nothing about weather, holidays, closures, price
changes or a trend, and says so in every answer.

Before any forecast is shown it is tested on the most recent days of the history: the pattern
is built from the days before them, used to "predict" them, and compared with what was actually
measured. The same days are also predicted with a naive rule (copy the same time one week
earlier), so the answer says whether the method does better than that. If it does not, the
answer says so too. The test is only as good as the history, and a short history gives a weak
test.

Everything is in UTC, like all readings here.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from energyops.calculations import kw_to_kwh, validate_readings
from energyops.synthetic import MINUTES_PER_DAY, MeterReading

MIN_COMPLETE_DAYS = 14
MIN_PER_WEEKDAY = 2
MAX_HORIZON_DAYS = 14
HOLDOUT_DAYS = 7

METHOD = (
    "Median power for each weekday and time of day, taken from complete past days of the same "
    "weekday, repeated forward."
)
LIMITS = (
    "This forecast uses only past meter readings. It does not know about weather, public "
    "holidays, closures, events, price changes or equipment changes, and it assumes no trend.",
    "All times are UTC.",
)


class ForecastRefused(ValueError):
    """There is not enough trustworthy history to forecast. The message says what is missing."""


@dataclass(frozen=True, slots=True)
class ForecastPoint:
    timestamp: datetime
    power_kw: float


@dataclass(frozen=True, slots=True)
class ForecastDay:
    day: date
    energy_kwh: float
    peak_kw: float


@dataclass(frozen=True, slots=True)
class Backtest:
    """How the method did on days it was not allowed to see."""

    days_tested: int
    mae_kw: float  # mean absolute error over every interval
    mae_percent_of_mean: float  # that error as a share of the mean measured power
    daily_energy_error_percent: float  # mean of |predicted - measured| / measured, per day
    naive_mae_kw: float | None
    beats_naive: bool | None  # None when there is nothing to compare with


@dataclass(frozen=True, slots=True)
class Forecast:
    method: str
    history_days: int  # complete days the pattern was built from
    interval_minutes: int
    days: list[ForecastDay]
    points: list[ForecastPoint]
    backtest: Backtest | None
    backtest_note: str | None  # why there is no backtest, when there is none
    limits: tuple[str, ...] = LIMITS


Profile = dict[tuple[int, int], float]  # (weekday, slot) -> median kW
DayValues = dict[date, list[float]]  # complete days only, one value per slot


def _complete_days(readings: Sequence[MeterReading]) -> tuple[DayValues, int, date]:
    interval = validate_readings(readings)
    slots = MINUTES_PER_DAY // interval
    grouped: dict[date, dict[int, float]] = {}
    for r in readings:
        slot = (r.timestamp.hour * 60 + r.timestamp.minute) // interval
        grouped.setdefault(r.timestamp.date(), {})[slot] = r.power_kw
    last_day = max(grouped)
    complete = {
        day: [values[s] for s in range(slots)]
        for day, values in grouped.items()
        if len(values) == slots
    }
    return complete, interval, last_day


def _build_profile(days: DayValues) -> Profile:
    if len(days) < MIN_COMPLETE_DAYS:
        raise ForecastRefused(
            f"Need at least {MIN_COMPLETE_DAYS} complete days of readings to forecast; "
            f"there are {len(days)}."
        )
    by_weekday: dict[int, list[list[float]]] = {}
    for day, values in days.items():
        by_weekday.setdefault(day.weekday(), []).append(values)
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    for weekday in range(7):
        have = len(by_weekday.get(weekday, []))
        if have < MIN_PER_WEEKDAY:
            raise ForecastRefused(
                f"Need at least {MIN_PER_WEEKDAY} complete {names[weekday]}s to forecast; "
                f"there {'is' if have == 1 else 'are'} {have}."
            )
    profile: Profile = {}
    for weekday, rows in by_weekday.items():
        for slot in range(len(rows[0])):
            profile[(weekday, slot)] = statistics.median(row[slot] for row in rows)
    return profile


def _predict_day(profile: Profile, day: date, slots: int) -> list[float]:
    return [profile[(day.weekday(), s)] for s in range(slots)]


def _backtest(complete: DayValues, slots: int) -> tuple[Backtest | None, str | None]:
    ordered = sorted(complete)
    holdout = ordered[-HOLDOUT_DAYS:]
    train = {d: complete[d] for d in ordered if d < holdout[0]}
    try:
        profile = _build_profile(train)
    except ForecastRefused as exc:
        return None, f"Not enough earlier history to test the method on recent days. {exc}"

    errors: list[float] = []
    naive_errors: list[float] = []
    measured_total = 0.0
    measured_count = 0
    daily_errors: list[float] = []
    tested = 0
    for day in holdout:
        actual = complete[day]
        predicted = _predict_day(profile, day, slots)
        lagged = complete.get(day - timedelta(days=7))
        if lagged is None:
            continue  # compare the two methods only on days both can predict
        tested += 1
        errors.extend(abs(p - a) for p, a in zip(predicted, actual, strict=True))
        naive_errors.extend(abs(n - a) for n, a in zip(lagged, actual, strict=True))
        measured_total += math.fsum(actual)
        measured_count += len(actual)
        actual_sum, predicted_sum = math.fsum(actual), math.fsum(predicted)
        if actual_sum > 0:
            daily_errors.append(abs(predicted_sum - actual_sum) / actual_sum)
    if tested == 0:
        return None, "None of the recent days had the same weekday a week earlier to compare with."

    mae = math.fsum(errors) / len(errors)
    mean_measured = measured_total / measured_count if measured_count else 0.0
    naive_mae = math.fsum(naive_errors) / len(naive_errors)
    return (
        Backtest(
            days_tested=tested,
            mae_kw=mae,
            mae_percent_of_mean=100 * mae / mean_measured if mean_measured > 0 else math.nan,
            daily_energy_error_percent=(
                100 * math.fsum(daily_errors) / len(daily_errors) if daily_errors else math.nan
            ),
            naive_mae_kw=naive_mae,
            beats_naive=mae < naive_mae,
        ),
        None,
    )


def forecast_demand(readings: Sequence[MeterReading], *, days: int = 7) -> Forecast:
    """Forecast the `days` days after the last reading. Raises ForecastRefused when the history
    is too short, and EnergyDataError when the readings cannot be trusted at all."""
    if not 1 <= days <= MAX_HORIZON_DAYS:
        raise ValueError(f"days must be between 1 and {MAX_HORIZON_DAYS}")
    if not readings:
        raise ForecastRefused("There are no readings to forecast from.")
    complete, interval, last_day = _complete_days(readings)
    profile = _build_profile(complete)
    slots = MINUTES_PER_DAY // interval
    backtest, note = _backtest(complete, slots)

    points: list[ForecastPoint] = []
    out_days: list[ForecastDay] = []
    for offset in range(1, days + 1):
        day = last_day + timedelta(days=offset)
        values = _predict_day(profile, day, slots)
        start = datetime(day.year, day.month, day.day, tzinfo=UTC)
        points.extend(
            ForecastPoint(start + timedelta(minutes=interval * s), v) for s, v in enumerate(values)
        )
        out_days.append(
            ForecastDay(
                day=day,
                energy_kwh=math.fsum(kw_to_kwh(v, interval) for v in values),
                peak_kw=max(values),
            )
        )
    return Forecast(
        method=METHOD,
        history_days=len(complete),
        interval_minutes=interval,
        days=out_days,
        points=points,
        backtest=backtest,
        backtest_note=note,
    )
