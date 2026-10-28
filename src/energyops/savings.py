"""Cost and saving estimates, computed in plain Python from readings and a tariff.

The savings advisor never calculates anything. This module computes every figure it may quote,
and the advisor's job is to pick among them and explain them. Every saving here is an estimate
built from a stated assumption (for example "the night time load is cut by 10% to 20%"). The
assumptions are facts on the sheet in their own right so they can be quoted, and nothing here
has been checked by a real before and after comparison, so nothing here is a promise.

Levers appear only when the data supports them: a night time lever when there is night time use,
a demand lever when the tariff has a demand charge, a load shifting lever when the tariff has
more than one price, and an events lever when the detector finds spikes. The window is the
complete days in the readings, scaled to a month of 30 days.
"""

from __future__ import annotations

import math
import re
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from energyops.anomalies import detect_anomalies
from energyops.calculations import EnergyDataError, daily_energy_kwh, kw_to_kwh, validate_readings
from energyops.facts import Fact, FactSheet
from energyops.synthetic import MeterReading
from energyops.tariffs import DEFAULT_BAND, Tariff, cost_of

MONTH_DAYS = 30
MIN_COMPLETE_DAYS = 3
NIGHT_END_HOUR = 5  # the night window is 00:00 to 05:00 UTC

# Assumed shares, low end and high end. They are assumptions, shown as facts, never measurements.
NIGHT_REDUCTION = (10.0, 20.0)
PEAK_REDUCTION = (5.0, 10.0)
SHIFT_SHARE = (5.0, 10.0)
EVENT_AVOIDED = (50.0, 100.0)

DAYS_TEXT = {"all": "every day", "weekday": "weekdays only", "weekend": "weekends only"}
SAVING_KEY = re.compile(r"_saving_cost_(low|high)$")


@dataclass(frozen=True, slots=True)
class SavingsSheet(FactSheet):
    """A fact sheet of costs and estimated savings. `preface` says what they are, in words."""

    preface: str = ""

    def to_prompt_text(self) -> str:
        return f"{self.preface}\n\n{super().to_prompt_text()}"

    @property
    def lever_count(self) -> int:
        return sum(1 for f in self.facts if f.key.endswith("_saving_cost_low"))


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def build_savings_sheet(readings: Sequence[MeterReading], tariff: Tariff) -> SavingsSheet:
    """Compute the cost facts and the estimated saving levers. Raises EnergyDataError if the
    readings cannot be trusted or cover fewer than three complete days."""
    interval = validate_readings(readings)
    days = daily_energy_kwh(readings)
    complete_days = {d.day for d in days if d.complete}
    if len(complete_days) < MIN_COMPLETE_DAYS:
        raise EnergyDataError(
            f"cost estimates need at least {MIN_COMPLETE_DAYS} complete days of readings, "
            f"found {len(complete_days)}"
        )
    used = sorted((r for r in readings if r.timestamp.date() in complete_days), key=_when)
    n = len(complete_days)
    symbol = tariff.symbol
    cost = cost_of(used, tariff)

    facts: list[Fact] = []
    warnings: list[str] = []
    skipped = [d.day.isoformat() for d in days if not d.complete]
    if skipped:
        warnings.append(
            "Days with missing readings are left out of every figure below: " + ", ".join(skipped)
        )

    def add(key: str, description: str, value: float, unit: str) -> None:
        facts.append(Fact(f"F{len(facts) + 1}", key, description, value, unit))

    add("days_used", "Complete days of readings used for every figure below", float(n), "days")
    add(
        "month_length",
        "Length of the month that monthly figures are scaled to (an assumption)",
        float(MONTH_DAYS),
        "days",
    )
    add("period_cost", "Estimated energy cost over those days", cost.total, symbol)
    daily_cost = cost.total / n
    add("average_daily_cost", "Estimated energy cost per day", daily_cost, symbol)
    add(
        "monthly_cost",
        f"Estimated energy cost for a month of {MONTH_DAYS} days at this rate",
        daily_cost * MONTH_DAYS,
        symbol,
    )
    add(
        "monthly_energy",
        f"Energy for a month of {MONTH_DAYS} days at this rate",
        cost.energy_kwh / n * MONTH_DAYS,
        "kWh",
    )
    add("tariff_standard_price", "Standard price per kWh", tariff.price_per_kwh, symbol)
    for band in tariff.bands:
        add(
            f"tariff_price_{_slug(band.name)}",
            f"Price per kWh in the {band.name} band "
            f"({band.start_hour:02d}:00 to {band.end_hour:02d}:00 UTC, {DAYS_TEXT[band.days]})",
            band.price_per_kwh,
            symbol,
        )

    scale = MONTH_DAYS / n

    def lever_range(
        stem: str,
        label: str,
        base_kwh: float,
        base_cost: float,
        share: tuple[float, float],
        *,
        saves_energy: bool = True,
    ) -> None:
        """Add the assumed shares and the monthly saving they would give, low then high.
        Moving use to another time of day saves money but no energy, so it passes False."""
        low, high = share
        add(f"{stem}_assumed_low", f"{label}: assumed share, low end (an assumption)", low, "%")
        add(f"{stem}_assumed_high", f"{label}: assumed share, high end (an assumption)", high, "%")
        if saves_energy:
            add(
                f"{stem}_saving_kwh_low",
                f"{label}: estimated energy saved in {MONTH_DAYS} days, low end",
                base_kwh * low / 100 * scale,
                "kWh",
            )
            add(
                f"{stem}_saving_kwh_high",
                f"{label}: estimated energy saved in {MONTH_DAYS} days, high end",
                base_kwh * high / 100 * scale,
                "kWh",
            )
        add(
            f"{stem}_saving_cost_low",
            f"{label}: estimated cost saved in {MONTH_DAYS} days, low end",
            base_cost * low / 100 * scale,
            symbol,
        )
        add(
            f"{stem}_saving_cost_high",
            f"{label}: estimated cost saved in {MONTH_DAYS} days, high end",
            base_cost * high / 100 * scale,
            symbol,
        )

    # Unusual events are looked at on their own (lever 4), so the night time lever leaves their
    # readings out and the same energy is never counted twice.
    try:
        events = detect_anomalies(used).anomalies
    except EnergyDataError:  # pragma: no cover - the readings were validated above
        events = ()
    unusual = {
        event.start + timedelta(minutes=interval * i)
        for event in events
        for i in range(event.intervals)
    }

    # Lever 1: the night time base load.
    night = [r for r in used if r.timestamp.hour < NIGHT_END_HOUR and r.timestamp not in unusual]
    night_kwh = math.fsum(kw_to_kwh(r.power_kw, interval) for r in night)
    if night and night_kwh > 0 and cost.energy_kwh > 0:
        night_cost = cost_of(night, tariff).total
        add(
            "night_load",
            f"Typical (median) demand between 00:00 and {NIGHT_END_HOUR:02d}:00 UTC",
            statistics.median(r.power_kw for r in night),
            "kW",
        )
        add(
            "night_share",
            f"Share of all energy used between 00:00 and {NIGHT_END_HOUR:02d}:00 UTC",
            night_kwh / cost.energy_kwh * 100,
            "%",
        )
        lever_range(
            "night",
            "Night time base load lever (cut the always-on night load, unusual events left out)",
            night_kwh,
            night_cost,
            NIGHT_REDUCTION,
        )

    # Lever 2: peak demand, only when the tariff charges for it.
    peak_kw = max(r.power_kw for r in used)
    if tariff.demand_charge_per_kw_month > 0 and peak_kw > 0:
        charge = tariff.demand_charge_per_kw_month
        add("peak_demand", "Highest average demand in one interval", peak_kw, "kW")
        add(
            "demand_charge",
            "Demand charge per kW per month (assuming each month peaks like this period)",
            charge,
            symbol,
        )
        low, high = PEAK_REDUCTION
        add("peak_assumed_low", "Peak demand lever: assumed cut, low end (an assumption)", low, "%")
        add(
            "peak_assumed_high",
            "Peak demand lever: assumed cut, high end (an assumption)",
            high,
            "%",
        )
        add(
            "peak_saving_cost_low",
            "Peak demand lever: estimated cost saved in a month, low end",
            peak_kw * low / 100 * charge,
            symbol,
        )
        add(
            "peak_saving_cost_high",
            "Peak demand lever: estimated cost saved in a month, high end",
            peak_kw * high / 100 * charge,
            symbol,
        )

    # Lever 3: shifting some use out of the dearest band, only when prices differ.
    prices = tariff.band_prices()
    if len(set(prices.values())) > 1:
        dear = max(prices, key=lambda name: prices[name])
        cheap = min(prices, key=lambda name: prices[name])
        dear_kwh = cost.by_band.get(dear, (0.0, 0.0))[0]
        gap = prices[dear] - prices[cheap]
        if dear_kwh > 0 and gap > 0:
            add(
                "dear_band_share",
                f"Share of all energy used in the dearest band ({dear})",
                dear_kwh / cost.energy_kwh * 100,
                "%",
            )
            add("price_gap", f"Price difference per kWh between {dear} and {cheap}", gap, symbol)
            lever_range(
                "shift",
                f"Load shifting lever (move use from {dear} to {cheap})",
                dear_kwh,
                dear_kwh * gap,
                SHIFT_SHARE,
                saves_energy=False,
            )

    # Lever 4: unusual spikes that might recur.
    spike_kwh = math.fsum(e.energy_difference_kwh for e in events if e.kind == "spike")
    if spike_kwh > 0 and cost.energy_kwh > 0:
        average_price = cost.total / cost.energy_kwh
        add(
            "spike_excess",
            "Extra energy used during unusual spikes in these days, above the usual pattern",
            spike_kwh,
            "kWh",
        )
        lever_range(
            "spikes",
            "Unusual spikes lever (avoid spikes like these recurring)",
            spike_kwh,
            spike_kwh * average_price,
            EVENT_AVOIDED,
        )

    if not any(SAVING_KEY.search(f.key) for f in facts):
        warnings.append(
            "No saving lever applies to this site's data and tariff, so there is nothing to "
            "estimate. Costs are still given."
        )

    return SavingsSheet(
        meter_id=used[0].meter_id,
        first_reading=used[0].timestamp,
        last_reading=used[-1].timestamp,
        facts=tuple(facts),
        warnings=tuple(warnings),
        preface=_preface(tariff),
    )


def _when(reading: MeterReading) -> datetime:
    return reading.timestamp


def _preface(tariff: Tariff) -> str:
    demo = (
        " This tariff is invented for the demo and is not a real supplier's price."
        if tariff.is_demo
        else ""
    )
    bands = (
        ", ".join(f"{b.name}" for b in tariff.bands) + f", otherwise {DEFAULT_BAND}"
        if tariff.bands
        else DEFAULT_BAND
    )
    charge = " It has a demand charge." if tariff.demand_charge_per_kw_month > 0 else ""
    return (
        f"Tariff: {tariff.label}, in {tariff.currency}, price bands: {bands}.{charge}{demo}\n"
        "Every saving in the facts is an ESTIMATE made by applying an assumed share (also given "
        "as a fact) to use that was measured. None of it has been checked by a before and after "
        "comparison, and none of it is a promise."
    )


__all__ = [
    "MONTH_DAYS",
    "SavingsSheet",
    "build_savings_sheet",
]
