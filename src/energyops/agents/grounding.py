"""The grounding check: nothing an agent writes may contain a figure that Python did not compute.

Figures with an energy unit (kWh, kW), percentages, day counts ("14 days", "14-day") and anything
with a currency symbol are checked. A figure with kWh or kW must equal a computed fact with the
same unit, compared at the number of decimal places the writer used. A percentage must match a
computed percentage, and the sign is ignored because words like "lower" carry it. A day count
must match the days-covered fact. A currency amount must equal a computed fact in that same
currency, so it is rejected when the fact sheet holds no cost figures (every sheet except the
savings advisor's). Amounts written in words ("40 pounds") are rejected too, because they cannot
be matched reliably. Other units are rejected as well. Plain numbers without a unit, such as
dates, times and fact ids, are not figures and are skipped.

Minutes are checked only when the fact sheet holds a figure in minutes, which today is only an
anomaly event's duration. For every other sheet a number of minutes is skipped as before, so
adding this changed nothing for the agents that have no such fact.
"""

from __future__ import annotations

import re

from energyops.facts import FactSheet

_ID = re.compile(r"\bF\d+\b")
_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:\d{2}|Z)?)?")
_CLOCK = re.compile(r"\b\d{1,2}:\d{2}\b")
_FIGURE = re.compile(
    r"(?P<cur>[£$€])?\s?(?P<num>\d[\d,]*(?:\.\d+)?)[\s-]?"
    r"(?P<unit>kWh|MWh|GWh|kW|MW|GW|W|%|days?|minutes?|mins?)?(?![A-Za-z])",
    re.IGNORECASE,
)
_WORD_CURRENCY = re.compile(
    r"\d[\d,]*(?:\.\d+)?\s?(?:pounds?|quid|GBP|EUR|USD|euros?|dollars?|pence)\b", re.IGNORECASE
)
_CHECKED_UNITS = {
    "kwh": "kWh",
    "kw": "kW",
    "%": "%",
    "day": "days",
    "days": "days",
    "min": "minutes",
    "mins": "minutes",
    "minute": "minutes",
    "minutes": "minutes",
}


def _clean(text: str) -> str:
    for pattern in (_DATETIME, _CLOCK, _ID):
        text = pattern.sub(" ", text)
    return text


def check_text(text: str, sheet: FactSheet) -> list[str]:
    """Return one problem per figure in `text` that is not backed by the fact sheet."""
    problems: list[str] = []
    for word in _WORD_CURRENCY.finditer(text):
        problems.append(
            f"'{word.group(0)}': write money with its currency symbol, exactly as in the fact sheet"
        )
    for match in _FIGURE.finditer(_clean(text)):
        shown = match.group(0).strip()
        unit = (match.group("unit") or "").lower()
        if match.group("cur"):
            symbol = match.group("cur")
            candidates = [f.value for f in sheet.facts if f.unit == symbol]
            if not candidates:
                problems.append(f"'{shown}': the fact sheet has no cost figures")
                continue
            number = match.group("num").replace(",", "")
            decimals = len(number.split(".")[1]) if "." in number else 0
            if not any(_same(float(number), c, decimals, ignore_sign=False) for c in candidates):
                problems.append(f"'{shown}' does not match any computed {symbol} figure")
            continue
        if not unit:
            continue
        if _CHECKED_UNITS.get(unit) == "minutes" and not any(
            f.unit == "minutes" for f in sheet.facts
        ):
            continue
        if unit not in _CHECKED_UNITS:
            problems.append(f"'{shown}': use kWh, kW, % or days exactly as in the fact sheet")
            continue
        canonical = _CHECKED_UNITS[unit]
        number = match.group("num").replace(",", "")
        decimals = len(number.split(".")[1]) if "." in number else 0
        value = float(number)
        candidates = [f.value for f in sheet.facts if f.unit == canonical]
        if not any(_same(value, c, decimals, ignore_sign=canonical == "%") for c in candidates):
            problems.append(f"'{shown}' does not match any computed {canonical} figure")
    return problems


def _same(written: float, computed: float, decimals: int, *, ignore_sign: bool) -> bool:
    if ignore_sign:
        written, computed = abs(written), abs(computed)
    return abs(round(computed, decimals) - written) < 1e-9


def check_fact_ids(fact_ids: list[str], sheet: FactSheet) -> list[str]:
    return [f"'{i}' is not a fact id in the fact sheet" for i in fact_ids if sheet.by_id(i) is None]
