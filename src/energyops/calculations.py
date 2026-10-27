"""Energy calculations as plain Python.

Power (kW) is a rate. Energy (kWh) is power over time. These functions are the only place the
two meet, and no model is ever asked to do this arithmetic.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from energyops.synthetic import MINUTES_PER_DAY, MeterReading


class EnergyDataError(ValueError):
    """The readings cannot be turned into a trustworthy energy figure."""


@dataclass(frozen=True, slots=True)
class DailyEnergy:
    """Energy for one UTC day. `complete` is False when some intervals are missing."""

    day: date
    energy_kwh: float
    intervals: int
    complete: bool


def kw_to_kwh(power_kw: float, interval_minutes: int) -> float:
    """Energy in kWh from average power in kW held for `interval_minutes`."""
    if interval_minutes <= 0:
        raise EnergyDataError("interval_minutes must be positive")
    if not math.isfinite(power_kw):
        raise EnergyDataError("power_kw must be a finite number")
    return power_kw * interval_minutes / 60


def validate_readings(readings: Sequence[MeterReading]) -> int:
    """Check that readings from one meter can be trusted, and return their interval in minutes.

    Raises EnergyDataError for no readings, more than one meter or interval length, an interval
    that does not divide a day, non-UTC or off-boundary timestamps, duplicate timestamps and
    power values that are not finite numbers.
    """
    if not readings:
        raise EnergyDataError("no readings")
    meter_id = readings[0].meter_id
    interval = readings[0].interval_minutes
    if interval <= 0 or MINUTES_PER_DAY % interval != 0:
        raise EnergyDataError("interval_minutes must divide 1440 evenly")

    seen: set[datetime] = set()
    for reading in readings:
        if reading.meter_id != meter_id:
            raise EnergyDataError("readings come from more than one meter")
        if reading.interval_minutes != interval:
            raise EnergyDataError("readings use more than one interval length")
        if reading.timestamp.utcoffset() != timedelta(0):
            raise EnergyDataError("timestamps must be in UTC")
        if reading.timestamp in seen:
            raise EnergyDataError(f"duplicate timestamp {reading.timestamp.isoformat()}")
        seen.add(reading.timestamp)
        minute = reading.timestamp.hour * 60 + reading.timestamp.minute
        if reading.timestamp.second or minute % interval:
            raise EnergyDataError("timestamp is not on an interval boundary")
        if not math.isfinite(reading.power_kw):
            raise EnergyDataError("power_kw must be a finite number")
    return interval


def daily_energy_kwh(readings: Sequence[MeterReading]) -> list[DailyEnergy]:
    """Total energy per UTC day for the readings of one meter, oldest day first.

    A reading belongs to the day its interval starts in. See `validate_readings` for what is
    rejected. An empty list gives no days.
    """
    if not readings:
        return []
    interval = validate_readings(readings)

    per_day: dict[date, list[float]] = {}
    for reading in readings:
        per_day.setdefault(reading.timestamp.date(), []).append(
            kw_to_kwh(reading.power_kw, interval)
        )

    expected = MINUTES_PER_DAY // interval
    return [
        DailyEnergy(
            day=day,
            energy_kwh=math.fsum(values),
            intervals=len(values),
            complete=len(values) == expected,
        )
        for day, values in sorted(per_day.items())
    ]


@dataclass(frozen=True, slots=True)
class DemandPoint:
    """A power level (kW, a rate) and the start of the interval it was recorded in."""

    power_kw: float
    timestamp: datetime


def peak_demand(readings: Sequence[MeterReading]) -> DemandPoint:
    """The highest average power in any interval. The earliest interval wins a tie."""
    validate_readings(readings)
    best = max(readings, key=lambda r: (r.power_kw, -r.timestamp.timestamp()))
    return DemandPoint(best.power_kw, best.timestamp)


def lowest_demand(readings: Sequence[MeterReading]) -> DemandPoint:
    """The lowest average power in any interval. The earliest interval wins a tie."""
    validate_readings(readings)
    best = min(readings, key=lambda r: (r.power_kw, r.timestamp.timestamp()))
    return DemandPoint(best.power_kw, best.timestamp)


def average_daily_kwh(days: Sequence[DailyEnergy], *, weekend: bool | None = None) -> float | None:
    """Mean energy per day over complete days only, or None if there are none.

    `weekend=True` keeps Saturdays and Sundays, `weekend=False` keeps Monday to Friday, and
    `None` keeps every complete day. Partial days are skipped because they would pull the
    average down.
    """
    chosen = [
        d.energy_kwh
        for d in days
        if d.complete and (weekend is None or (d.day.weekday() >= 5) == weekend)
    ]
    return math.fsum(chosen) / len(chosen) if chosen else None
