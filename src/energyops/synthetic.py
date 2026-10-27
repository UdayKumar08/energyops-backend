"""Deterministic synthetic meter data.

Everything here is made up. The same seed, meter id and settings always give the same readings,
so tests and demos are repeatable. All timestamps are UTC, so daylight saving never changes the
number of readings in a day.

A reading holds average power in kW over one interval. Energy (kWh) is a different quantity and
is calculated in `energyops.calculations`, never stored here.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

MINUTES_PER_DAY = 24 * 60


@dataclass(frozen=True, slots=True)
class MeterReading:
    """Average power for one meter over one interval starting at `timestamp`."""

    meter_id: str
    timestamp: datetime
    interval_minutes: int
    power_kw: float


@dataclass(frozen=True, slots=True)
class SiteProfile:
    """A made-up site: constant base load plus a daytime peak, lower at weekends."""

    base_load_kw: float = 20.0
    peak_extra_kw: float = 60.0
    weekend_factor: float = 0.5
    noise_fraction: float = 0.05

    def __post_init__(self) -> None:
        if self.base_load_kw < 0 or self.peak_extra_kw < 0:
            raise ValueError("base_load_kw and peak_extra_kw must not be negative")
        if not 0 <= self.weekend_factor <= 1:
            raise ValueError("weekend_factor must be between 0 and 1")
        if not 0 <= self.noise_fraction <= 1:
            raise ValueError("noise_fraction must be between 0 and 1")


def _daytime_shape(minute_of_day: int) -> float:
    """0 at night, rising to 1 at 13:00 and falling back to 0 by 20:00."""
    hour = minute_of_day / 60
    if hour < 6 or hour > 20:
        return 0.0
    return math.sin(math.pi * (hour - 6) / 14)


def generate_readings(
    *,
    seed: int,
    meter_id: str,
    start: datetime,
    days: int,
    interval_minutes: int = 15,
    profile: SiteProfile | None = None,
) -> list[MeterReading]:
    """Return `days` whole days of readings for one meter, starting at `start`.

    `start` must be timezone aware and sit exactly on an interval boundary, and
    `interval_minutes` must divide a day evenly, so that no interval crosses midnight.
    """
    if days < 1:
        raise ValueError("days must be at least 1")
    if interval_minutes < 1 or MINUTES_PER_DAY % interval_minutes != 0:
        raise ValueError("interval_minutes must divide 1440 evenly")
    if not meter_id:
        raise ValueError("meter_id must not be empty")
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("start must be timezone aware")
    start_utc = start.astimezone(UTC)
    minute_of_day = start_utc.hour * 60 + start_utc.minute
    if start_utc.second or start_utc.microsecond or minute_of_day % interval_minutes:
        raise ValueError("start must sit exactly on an interval boundary")

    site = profile or SiteProfile()
    # A local Random instance, so the global random state is never read or changed.
    rng = random.Random(f"{seed}:{meter_id}")
    step = timedelta(minutes=interval_minutes)
    count = days * MINUTES_PER_DAY // interval_minutes

    readings: list[MeterReading] = []
    for i in range(count):
        moment = start_utc + i * step
        minute = moment.hour * 60 + moment.minute
        day_scale = site.weekend_factor if moment.weekday() >= 5 else 1.0
        load = site.base_load_kw + site.peak_extra_kw * day_scale * _daytime_shape(minute)
        noisy = load + rng.gauss(0.0, site.noise_fraction * load)
        readings.append(
            MeterReading(
                meter_id=meter_id,
                timestamp=moment,
                interval_minutes=interval_minutes,
                power_kw=round(max(0.0, noisy), 3),
            )
        )
    return readings
