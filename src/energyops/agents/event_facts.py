"""The facts about one anomaly event, for the anomaly investigator.

An event comes from the deterministic detector (see anomalies.py), either called directly or
through the read-only `get_anomalies` MCP tool. Python then turns it into a small fact sheet of
its own: how big the event was, how long it lasted, how it compares with the site's usual
demand and with an average day. The model is shown only that sheet and every figure it writes is
checked against it, exactly as for the other agents.

Sizes are written as positive figures and the direction (a spike or a drop) is said in words,
because the figure check compares kWh and kW values with their sign and a model would naturally
write "dropped by 59.1 kW", not "-59.1 kW".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pydantic import ValidationError

from energyops.agents.fact_sources import (
    McpToolSource,
    ToolAccessError,
)
from energyops.agents.runs import TraceRecorder
from energyops.agents.sources import ReadingsSource
from energyops.anomalies import detect_anomalies
from energyops.facts import Fact, FactSheet, build_fact_sheet
from energyops.mcp_servers.schema import AnomalyListModel, AnomalyModel, FactSheetModel


class UnknownAnomalyError(LookupError):
    """The site has no event with that id."""

    error_kind = "unknown_anomaly"


@dataclass(frozen=True, slots=True)
class EventSheet(FactSheet):
    """A fact sheet about one event. `event_text` says what the event is, in words."""

    event_text: str = ""

    def to_prompt_text(self) -> str:
        return f"{self.event_text}\n\n{super().to_prompt_text()}"


class EventFactsProvider(Protocol):
    via: str

    async def __call__(
        self, site: str | None, anomaly_id: str, trace: TraceRecorder
    ) -> EventSheet: ...


def pick_event(events: AnomalyListModel, anomaly_id: str) -> AnomalyModel:
    for event in events.anomalies:
        if event.id == anomaly_id:
            return event
    known = ", ".join(e.id for e in events.anomalies) or "none"
    raise UnknownAnomalyError(
        f"there is no event {anomaly_id} at this site (events found: {known})"
    )


def build_event_sheet(site_sheet: FactSheet, event: AnomalyModel) -> EventSheet:
    """Turn one detector event and its site's fact sheet into the sheet the model is shown."""
    start, end = datetime.fromisoformat(event.start), datetime.fromisoformat(event.end)
    peak_at = datetime.fromisoformat(event.peak_at)
    facts: list[Fact] = []

    def add(
        key: str, description: str, value: float, unit: str, when: datetime | None = None
    ) -> None:
        facts.append(Fact(f"F{len(facts) + 1}", key, description, value, unit, when))

    size_kwh = abs(event.energy_difference_kwh)
    add(
        "event_energy_difference",
        "Energy over the whole event compared with the usual pattern at those times "
        "(higher for a spike, lower for a drop)",
        size_kwh,
        "kWh",
    )
    add(
        "event_peak_difference",
        "Largest difference from the usual demand in one interval",
        abs(event.peak_deviation_kw),
        "kW",
    )
    add(
        "event_demand_at_peak",
        "Demand at the most unusual interval",
        event.observed_kw_at_peak,
        "kW",
        peak_at,
    )
    add(
        "event_usual_demand",
        "Usual demand at that time of day on other days of the same kind",
        event.baseline_kw_at_peak,
        "kW",
    )
    if event.baseline_kw_at_peak > 0:
        add(
            "event_peak_difference_percent",
            "Largest difference as a share of the usual demand",
            abs(event.peak_deviation_kw) / event.baseline_kw_at_peak * 100,
            "%",
        )
    add(
        "event_duration", "How long the event lasted", (end - start).total_seconds() / 60, "minutes"
    )

    average = next((f for f in site_sheet.facts if f.key == "average_daily_energy"), None)
    if average is not None:
        add("average_daily_energy", average.description, average.value, "kWh")
        if average.value > 0:
            add(
                "event_share_of_average_day",
                "Energy difference over the event as a share of an average day's energy",
                size_kwh / average.value * 100,
                "%",
            )
    covered = next((f for f in site_sheet.facts if f.key == "days_covered"), None)
    if covered is not None:
        add("days_covered", covered.description, covered.value, "days")

    direction = (
        "a spike: demand was higher than usual for this time of day"
        if event.kind == "spike"
        else "a drop: demand was lower than usual for this time of day"
    )
    event_text = (
        f"Event {event.id} at meter {site_sheet.meter_id} is {direction}, compared with the "
        f"same time of day on other days of the same kind (weekdays or weekend days).\n"
        f"It starts {start.isoformat()} and ends {end.isoformat()} (UTC, a {start:%A}). "
        "The detector only finds unusual readings. It does not know the cause."
    )
    return EventSheet(
        meter_id=site_sheet.meter_id,
        first_reading=site_sheet.first_reading,
        last_reading=site_sheet.last_reading,
        facts=tuple(facts),
        warnings=site_sheet.warnings,
        event_text=event_text,
    )


class DirectEventFacts:
    """Detect events and build the sheet in this process, without any tool call."""

    via = "direct"

    def __init__(self, source: ReadingsSource) -> None:
        self._source = source

    async def __call__(self, site: str | None, anomaly_id: str, trace: TraceRecorder) -> EventSheet:
        readings = self._source(site)
        events = AnomalyListModel.from_result(detect_anomalies(readings))
        return build_event_sheet(build_fact_sheet(readings), pick_event(events, anomaly_id))


class McpEventFacts(McpToolSource):
    """Ask the read-only energy MCP server for the site's events and its fact sheet."""

    async def __call__(self, site: str | None, anomaly_id: str, trace: TraceRecorder) -> EventSheet:
        needed = self._guard(site, ["get_anomalies", "get_fact_sheet"])
        kind, payload = await self._fetch(
            site, needed, trace, tools=("get_anomalies", "get_fact_sheet")
        )
        if kind != "ok":
            self._raise_failure(kind, payload)
        try:
            events = AnomalyListModel.model_validate(payload["get_anomalies"])
            sheet = FactSheetModel.model_validate(payload["get_fact_sheet"]).to_fact_sheet()
        except (ValidationError, ValueError):
            raise ToolAccessError("a tool returned its result in an unexpected shape") from None
        return build_event_sheet(sheet, pick_event(events, anomaly_id))
