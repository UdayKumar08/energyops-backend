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


def daily_energy_kwh(readings: Sequence[MeterReading]) -> list[DailyEnergy]:
    """Total energy per UTC day for the readings of one meter, oldest day first.

    A reading belongs to the day its interval starts in. Intervals must not cross midnight,
    timestamps must not repeat, and all readings must share one meter and one interval length.
    """
    if not readings:
        return []

    meter_id = readings[0].meter_id
    interval = readings[0].interval_minutes
    if interval <= 0 or MINUTES_PER_DAY % interval != 0:
        raise EnergyDataError("interval_minutes must divide 1440 evenly")

    seen: set[datetime] = set()
    per_day: dict[date, list[float]] = {}
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
