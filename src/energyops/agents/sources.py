"""Where an agent gets meter readings from.

For now the only source is synthetic demo data. Later sources will read a database and must
check that the signed-in user may see the site they ask for, which is why a source is a function
of the requested site and not something the model can reach into freely.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from energyops.synthetic import MeterReading, SiteProfile, generate_readings

ReadingsSource = Callable[[str | None], Sequence[MeterReading]]

DEMO_START = datetime(2026, 3, 2, tzinfo=UTC)  # a Monday
DEMO_SITES = ("demo-hotel", "demo-office", "demo-clinic")

# demo-clinic has two invented events written into its readings so there is something for the
# anomaly detector and the anomaly investigator to find: after-hours load on a Wednesday night,
# and a midday dropout the following week. They are not based on any real building.
CLINIC_PROFILE = SiteProfile(
    base_load_kw=15.0, peak_extra_kw=45.0, weekend_factor=0.6, noise_fraction=0.04
)
CLINIC_EVENTS = (
    (datetime(2026, 3, 4, 2, 0, tzinfo=UTC), timedelta(hours=2), lambda kw: kw + 35.0),
    (datetime(2026, 3, 12, 12, 0, tzinfo=UTC), timedelta(hours=1), lambda kw: 0.0),
)


def synthetic_demo_source(seed: int = 1, days: int = 14) -> ReadingsSource:
    """Invented readings for three demo sites. Not real data and not calibrated to any building."""

    def source(site: str | None) -> Sequence[MeterReading]:
        chosen = site or DEMO_SITES[0]
        if chosen not in DEMO_SITES:
            raise LookupError(f"unknown site {chosen!r}")
        if chosen != "demo-clinic":
            return generate_readings(seed=seed, meter_id=chosen, start=DEMO_START, days=days)
        readings = generate_readings(
            seed=seed, meter_id=chosen, start=DEMO_START, days=days, profile=CLINIC_PROFILE
        )
        for first, length, change in CLINIC_EVENTS:
            readings = [
                replace(r, power_kw=round(change(r.power_kw), 3))
                if first <= r.timestamp < first + length
                else r
                for r in readings
            ]
        return readings

    return source
