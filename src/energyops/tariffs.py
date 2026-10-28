"""Energy tariffs as plain Python, and the cost of a set of readings under one.

A tariff says what energy costs at each time of day. It is data entered by a person for a site
(or invented for the demo sites and labelled that way), never something a model supplies. All
times are UTC, like every other time in this product, so a time-of-use band that follows local
clock time will be an hour out while daylight saving time is in force. That is a known limit, not
a hidden one.

Money is kept as a float for estimates only. These figures are estimates for decision support
and are never an invoice.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from energyops.calculations import EnergyDataError, kw_to_kwh, validate_readings
from energyops.synthetic import MeterReading

CURRENCY_SYMBOLS = {"GBP": "£", "EUR": "€", "USD": "$"}
DayKind = Literal["all", "weekday", "weekend"]
DEFAULT_BAND = "Standard"


class TariffError(ValueError):
    """The tariff is not usable."""


@dataclass(frozen=True, slots=True)
class TariffBand:
    """A price that applies from `start_hour` up to `end_hour` (UTC), on some kinds of day."""

    name: str
    start_hour: int
    end_hour: int
    price_per_kwh: float
    days: DayKind = "all"

    def __post_init__(self) -> None:
        if not self.name:
            raise TariffError("a band needs a name")
        if not (0 <= self.start_hour < self.end_hour <= 24):
            raise TariffError(
                f"band {self.name!r}: hours must satisfy 0 <= start < end <= 24 "
                "(split a band that crosses midnight into two)"
            )
        _check_price(self.price_per_kwh, f"band {self.name!r}")

    def applies(self, moment: datetime) -> bool:
        weekend = moment.weekday() >= 5
        if self.days == "weekday" and weekend:
            return False
        if self.days == "weekend" and not weekend:
            return False
        return self.start_hour <= moment.hour < self.end_hour


def _check_price(value: float, what: str) -> None:
    if not math.isfinite(value) or value < 0:
        raise TariffError(f"{what}: a price must be a finite number, not negative")


@dataclass(frozen=True, slots=True)
class Tariff:
    """What energy costs at a site. `price_per_kwh` applies wherever no band does."""

    label: str
    currency: Literal["GBP", "EUR", "USD"]
    price_per_kwh: float
    bands: tuple[TariffBand, ...] = ()
    demand_charge_per_kw_month: float = 0.0
    is_demo: bool = False

    def __post_init__(self) -> None:
        if not self.label:
            raise TariffError("a tariff needs a label")
        if self.currency not in CURRENCY_SYMBOLS:
            raise TariffError(f"currency must be one of {', '.join(CURRENCY_SYMBOLS)}")
        _check_price(self.price_per_kwh, "the standard price")
        _check_price(self.demand_charge_per_kw_month, "the demand charge")
        # Bands must not overlap, or a reading could have two prices.
        for hour in range(24):
            for weekend in (False, True):
                moment = datetime(2026, 3, 7 if weekend else 2, hour)  # a Saturday, a Monday
                hits = [b.name for b in self.bands if b.applies(moment)]
                if len(hits) > 1:
                    raise TariffError(f"bands overlap at {hour:02d}:00: {', '.join(hits)}")

    @property
    def symbol(self) -> str:
        return CURRENCY_SYMBOLS[self.currency]

    def band_at(self, moment: datetime) -> tuple[str, float]:
        """The band name and price per kWh that apply at `moment` (UTC)."""
        for band in self.bands:
            if band.applies(moment):
                return band.name, band.price_per_kwh
        return DEFAULT_BAND, self.price_per_kwh

    def band_prices(self) -> dict[str, float]:
        """Every distinct price this tariff can charge, by band name (the standard price too if
        any hour is not covered by a band)."""
        prices = {b.name: b.price_per_kwh for b in self.bands}
        for hour in range(24):
            for weekend in (False, True):
                moment = datetime(2026, 3, 7 if weekend else 2, hour)
                if self.band_at(moment)[0] == DEFAULT_BAND:
                    prices[DEFAULT_BAND] = self.price_per_kwh
        return prices


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    total: float
    energy_kwh: float
    by_band: dict[str, tuple[float, float]]  # band -> (kWh, cost)


def cost_of(readings: Sequence[MeterReading], tariff: Tariff) -> CostBreakdown:
    """Energy cost of the readings under the tariff, in total and by band."""
    interval = validate_readings(readings)
    by_band: dict[str, list[float]] = {}
    costs: dict[str, list[float]] = {}
    for reading in readings:
        name, price = tariff.band_at(reading.timestamp)
        kwh = kw_to_kwh(reading.power_kw, interval)
        by_band.setdefault(name, []).append(kwh)
        costs.setdefault(name, []).append(kwh * price)
    table = {name: (math.fsum(by_band[name]), math.fsum(costs[name])) for name in by_band}
    return CostBreakdown(
        total=math.fsum(c for _, c in table.values()),
        energy_kwh=math.fsum(k for k, _ in table.values()),
        by_band=table,
    )


def require_tariff(tariff: Tariff | None, site: str | None) -> Tariff:
    if tariff is None:
        raise NoTariffError(
            f"no tariff is set for {site or 'this site'}, so costs and savings cannot be estimated"
        )
    return tariff


class NoTariffError(LookupError):
    """The site has no tariff. Costs are never guessed."""

    error_kind = "no_tariff"


__all__ = [
    "CURRENCY_SYMBOLS",
    "DEFAULT_BAND",
    "CostBreakdown",
    "EnergyDataError",
    "NoTariffError",
    "Tariff",
    "TariffBand",
    "TariffError",
    "cost_of",
    "require_tariff",
]
