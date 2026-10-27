"""Where an agent gets meter readings from.

For now the only source is synthetic demo data. Later sources will read a database and must
check that the signed-in user may see the site they ask for, which is why a source is a function
of the requested site and not something the model can reach into freely.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from energyops.synthetic import MeterReading, generate_readings

ReadingsSource = Callable[[str | None], Sequence[MeterReading]]

DEMO_START = datetime(2026, 3, 2, tzinfo=UTC)  # a Monday
DEMO_SITES = ("demo-hotel", "demo-office")


def synthetic_demo_source(seed: int = 1, days: int = 14) -> ReadingsSource:
    """Invented readings for two demo sites. Not real data and not calibrated to any building."""

    def source(site: str | None) -> Sequence[MeterReading]:
        chosen = site or DEMO_SITES[0]
        if chosen not in DEMO_SITES:
            raise LookupError(f"unknown site {chosen!r}")
        return generate_readings(seed=seed, meter_id=chosen, start=DEMO_START, days=days)

    return source
