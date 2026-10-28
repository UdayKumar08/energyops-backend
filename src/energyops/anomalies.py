"""A deterministic anomaly detector for meter readings, in plain Python.

Each reading is compared with what the same meter did at the same time of day on the other days
of the same kind (weekdays against weekdays, weekend days against weekend days). The baseline is
the median of those other readings. A reading is flagged when it is far from that baseline in
three ways at once: many robust standard deviations away, a meaningful share of the baseline,
and a meaningful number of kW. Requiring all three keeps ordinary noise from being flagged. The
spread used for the first test is the larger of that time slot's own spread and a spread pooled
over the whole period as a share of the baseline, because a slot with only three to nine
comparable readings can by chance look much quieter than it really is.
Consecutive flagged readings of the same direction become one event.

This only finds unusual readings. It does not say why they happened and it never calls
anything a fault: explaining possible causes is the anomaly investigator's job, as hypotheses,
and a person decides what to do. A model is never asked to find anomalies.

Honest limits: it has been tried only on synthetic data, its thresholds are not calibrated to
any real building, a short history gives weak baselines (the default needs at least three other
comparable days), and a change that lasts for the whole period looks normal because there is
nothing to compare it with.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from energyops.calculations import EnergyDataError, validate_readings
from energyops.synthetic import MeterReading

# Turns a median absolute deviation into a standard deviation for normally distributed noise.
_MAD_TO_SIGMA = 1.4826


@dataclass(frozen=True, slots=True)
class DetectorSettings:
    min_comparable_readings: int = 3  # other days of the same kind that must have this time slot
    sigmas: float = 6.0  # robust standard deviations from the baseline
    min_relative: float = 0.20  # share of the baseline
    min_kw: float = 2.0  # absolute distance

    def __post_init__(self) -> None:
        if self.min_comparable_readings < 3:
            raise ValueError("min_comparable_readings must be at least 3")
        if self.sigmas <= 0 or self.min_relative < 0 or self.min_kw < 0:
            raise ValueError("sigmas must be positive and the minimums must not be negative")


@dataclass(frozen=True, slots=True)
class Anomaly:
    """One event: a run of unusual readings that all point the same way."""

    id: str
    kind: Literal["spike", "drop"]
    start: datetime
    end: datetime  # the end of the last unusual interval
    intervals: int
    peak_deviation_kw: float  # signed: observed minus baseline, at the most unusual interval
    peak_at: datetime
    observed_kw_at_peak: float
    baseline_kw_at_peak: float
    energy_difference_kwh: float  # signed total over the event, observed minus baseline


@dataclass(frozen=True, slots=True)
class DetectionResult:
    meter_id: str
    anomalies: tuple[Anomaly, ...]
    judged_readings: int
    unjudged_readings: int  # too little comparable history to judge
    settings: DetectorSettings


def _slot(reading: MeterReading) -> tuple[bool, int]:
    stamp = reading.timestamp
    return stamp.weekday() >= 5, stamp.hour * 60 + stamp.minute


def detect_anomalies(
    readings: Sequence[MeterReading], settings: DetectorSettings | None = None
) -> DetectionResult:
    """Find unusual readings for one meter. Raises EnergyDataError for untrustworthy readings."""
    chosen = settings or DetectorSettings()
    interval = validate_readings(readings)
    ordered = sorted(readings, key=lambda r: r.timestamp)

    by_slot: dict[tuple[bool, int], list[MeterReading]] = {}
    for reading in ordered:
        by_slot.setdefault(_slot(reading), []).append(reading)

    # First pass: a baseline and a slot spread for every reading that has enough history.
    judged_items: list[tuple[MeterReading, float, float, float]] = []  # reading, base, spread, dev
    unjudged = 0
    for reading in ordered:
        others = [r.power_kw for r in by_slot[_slot(reading)] if r is not reading]
        if len(others) < chosen.min_comparable_readings:
            unjudged += 1
            continue
        baseline = statistics.median(others)
        spread = _MAD_TO_SIGMA * statistics.median(abs(x - baseline) for x in others)
        judged_items.append((reading, baseline, spread, reading.power_kw - baseline))

    # The typical noise as a share of the baseline, pooled over every judged reading.
    shares = [abs(dev) / base for _, base, _, dev in judged_items if base > 0]
    pooled = _MAD_TO_SIGMA * statistics.median(shares) if shares else 0.0

    flagged: list[tuple[MeterReading, float, float]] = []  # reading, baseline, deviation
    for reading, baseline, spread, deviation in judged_items:
        scale = max(spread, pooled * abs(baseline))
        needed = max(chosen.sigmas * scale, chosen.min_relative * abs(baseline), chosen.min_kw)
        if abs(deviation) >= needed:
            flagged.append((reading, baseline, deviation))

    step = timedelta(minutes=interval)
    events: list[list[tuple[MeterReading, float, float]]] = []
    for item in flagged:
        reading, _, deviation = item
        if events:
            last_reading, _, last_deviation = events[-1][-1]
            if reading.timestamp - last_reading.timestamp == step and (deviation > 0) == (
                last_deviation > 0
            ):
                events[-1].append(item)
                continue
        events.append([item])

    anomalies = []
    for number, event in enumerate(events, start=1):
        peak_reading, peak_baseline, peak_deviation = max(event, key=lambda e: abs(e[2]))
        anomalies.append(
            Anomaly(
                id=f"A{number}",
                kind="spike" if peak_deviation > 0 else "drop",
                start=event[0][0].timestamp,
                end=event[-1][0].timestamp + step,
                intervals=len(event),
                peak_deviation_kw=peak_deviation,
                peak_at=peak_reading.timestamp,
                observed_kw_at_peak=peak_reading.power_kw,
                baseline_kw_at_peak=peak_baseline,
                energy_difference_kwh=math.fsum(d for _, _, d in event) * interval / 60,
            )
        )
    return DetectionResult(
        meter_id=ordered[0].meter_id,
        anomalies=tuple(anomalies),
        judged_readings=len(judged_items),
        unjudged_readings=unjudged,
        settings=chosen,
    )


__all__ = ["Anomaly", "DetectionResult", "DetectorSettings", "EnergyDataError", "detect_anomalies"]
