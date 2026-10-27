"""The fact sheet: every number an energy agent is allowed to quote.

Plain Python computes these figures from meter readings. A model is only ever shown this sheet
and asked to explain it, and anything it writes is checked against it afterwards. A figure that
cannot be computed is left out and the reason is recorded in `warnings`, never guessed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from energyops.calculations import (
    average_daily_kwh,
    daily_energy_kwh,
    lowest_demand,
    peak_demand,
    validate_readings,
)
from energyops.synthetic import MeterReading


@dataclass(frozen=True, slots=True)
class Fact:
    """One computed figure. `value` is in `unit`; `when` says which day or interval it is for."""

    id: str
    key: str
    description: str
    value: float
    unit: str
    when: date | datetime | None = None

    @property
    def display(self) -> str:
        """The figure exactly as it is shown to a model and expected back, one decimal place."""
        if self.unit == "%":
            return f"{self.value:.1f}%"
        if self.unit == "days":
            return f"{self.value:.0f} day" if self.value == 1 else f"{self.value:.0f} days"
        return f"{self.value:.1f} {self.unit}"


@dataclass(frozen=True, slots=True)
class FactSheet:
    meter_id: str
    first_reading: datetime
    last_reading: datetime
    facts: tuple[Fact, ...]
    warnings: tuple[str, ...]

    def by_id(self, fact_id: str) -> Fact | None:
        return next((f for f in self.facts if f.id == fact_id), None)

    def to_prompt_text(self) -> str:
        """A plain-text version for a prompt. Ids are what a model must cite."""
        lines = [
            f"Meter {self.meter_id}, readings from {self.first_reading.isoformat()} "
            f"to {self.last_reading.isoformat()} (UTC).",
        ]
        for fact in self.facts:
            when = f" ({fact.when.isoformat()})" if fact.when is not None else ""
            lines.append(f"{fact.id} {fact.description}: {fact.display}{when}")
        for warning in self.warnings:
            lines.append(f"Note: {warning}")
        return "\n".join(lines)


def build_fact_sheet(readings: Sequence[MeterReading]) -> FactSheet:
    """Compute the fact sheet for one meter's readings. Raises EnergyDataError if untrustworthy."""
    interval = validate_readings(readings)
    days = daily_energy_kwh(readings)
    complete = [d for d in days if d.complete]
    ordered = sorted(readings, key=lambda r: r.timestamp)

    facts: list[Fact] = []
    warnings: list[str] = []

    def add(
        key: str, description: str, value: float, unit: str, when: date | datetime | None = None
    ) -> None:
        facts.append(Fact(f"F{len(facts) + 1}", key, description, value, unit, when))

    add(
        "total_energy",
        f"Total energy over all {len(readings)} readings",
        sum(d.energy_kwh for d in days),
        "kWh",
    )

    incomplete = [d.day.isoformat() for d in days if not d.complete]
    if incomplete:
        warnings.append(
            "Days with missing readings are left out of daily figures: " + ", ".join(incomplete)
        )

    average = average_daily_kwh(days)
    if average is None:
        warnings.append(f"No complete day of {1440 // interval} readings, so no daily figures.")
    else:
        highest = max(complete, key=lambda d: (d.energy_kwh, -d.day.toordinal()))
        lowest = min(complete, key=lambda d: (d.energy_kwh, d.day.toordinal()))
        add("average_daily_energy", "Average energy per complete day", average, "kWh")
        add(
            "highest_day_energy",
            "Highest energy in a complete day",
            highest.energy_kwh,
            "kWh",
            highest.day,
        )
        add(
            "lowest_day_energy",
            "Lowest energy in a complete day",
            lowest.energy_kwh,
            "kWh",
            lowest.day,
        )

        weekday = average_daily_kwh(days, weekend=False)
        weekend = average_daily_kwh(days, weekend=True)
        if weekday is not None and weekend is not None:
            add("average_weekday_energy", "Average energy per complete weekday", weekday, "kWh")
            add("average_weekend_energy", "Average energy per complete weekend day", weekend, "kWh")
            if weekday > 0:
                add(
                    "weekend_vs_weekday_change",
                    "Weekend day energy compared with a weekday (negative means lower)",
                    (weekend / weekday - 1) * 100,
                    "%",
                )
        else:
            warnings.append("Weekday and weekend figures need a complete day of each kind.")

    peak = peak_demand(readings)
    low = lowest_demand(readings)
    add("peak_demand", "Peak average demand in one interval", peak.power_kw, "kW", peak.timestamp)
    add("lowest_demand", "Lowest average demand in one interval", low.power_kw, "kW", low.timestamp)
    # Added last so the ids of the figures above do not change. Counts calendar days (UTC) that
    # have any reading, complete or not, so a model can say how long the period is without guessing.
    add("days_covered", "Calendar days with readings (UTC)", float(len(days)), "days")

    return FactSheet(
        meter_id=ordered[0].meter_id,
        first_reading=ordered[0].timestamp,
        last_reading=ordered[-1].timestamp,
        facts=tuple(facts),
        warnings=tuple(warnings),
    )
