"""Drafting a maintenance ticket from a detected event, in plain Python.

The draft is built from a fixed template and the detector's measured figures. No model writes it,
so it cannot invent a cause or a figure. The list of things to check is generic: it says where a
person might look, never what went wrong, because the product does not know. A person approves,
changes or rejects the draft (see graphs/tickets.py) and nothing is sent anywhere outside this
product.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from energyops.mcp_servers.schema import AnomalyModel

PRIORITIES = ("low", "medium", "high")

CHECKS = {
    "spike": (
        "Find out what was running at that time, for example heating, cooling, lighting, "
        "kitchen or laundry equipment, or anything left switched on.",
        "Compare with the building's schedule for that day and ask whoever was on site.",
        "If the load repeats, look at the same hours on the following days.",
    ),
    "drop": (
        "Check whether the meter or its communications were working during that time, because a "
        "drop to zero is often a missing reading and not a real change in use.",
        "If the readings are real, check whether equipment tripped or was switched off.",
        "Compare with the building's schedule for that day.",
    ),
}


@dataclass(frozen=True, slots=True)
class TicketDraft:
    title: str
    description: str
    priority: str
    evidence: tuple[str, ...]


def _when(text: str) -> str:
    return datetime.fromisoformat(text).strftime("%a %d %b %Y, %H:%M UTC")


def draft_from_event(site_name: str, event: AnomalyModel) -> TicketDraft:
    """A draft ticket for one event. Priority starts at medium; the approver may change it."""
    kind_word = "unusually high" if event.kind == "spike" else "unusually low"
    title = f"Check {kind_word} demand at {site_name}, {_when(event.start)}"[:200]
    evidence = (
        f"Event {event.id} at {site_name}: {event.intervals} intervals starting "
        f"{_when(event.start)}.",
        f"At the most unusual point ({_when(event.peak_at)}) demand was "
        f"{event.observed_kw_at_peak:.1f} kW against {event.baseline_kw_at_peak:.1f} kW usually "
        "seen at that time of day.",
        f"Over the event the energy was {abs(event.energy_difference_kwh):.1f} kWh "
        f"{'more' if event.energy_difference_kwh >= 0 else 'less'} than usual.",
    )
    checks = "\n".join(f"{i}. {text}" for i, text in enumerate(CHECKS[event.kind], start=1))
    description = (
        "This ticket was drafted from readings the detector found unusual. The detector finds "
        "unusual readings only; it does not know the cause and has not found a fault.\n\n"
        "What was measured:\n" + "\n".join(f"- {line}" for line in evidence) + "\n\n"
        "Suggested checks:\n" + checks
    )
    return TicketDraft(title=title, description=description, priority="medium", evidence=evidence)
