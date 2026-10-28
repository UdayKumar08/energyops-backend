"""The shape a fact sheet has when it travels over MCP, and the way back to a FactSheet.

Everything is plain JSON types, so a client in any language can read it. `to_fact_sheet` rebuilds
the exact object the server computed, which is what lets an agent use a fact sheet it fetched
through a tool with the same grounding check it uses for one it computed itself.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel

from energyops.anomalies import DetectionResult
from energyops.facts import Fact, FactSheet
from energyops.savings import SavingsSheet


class FactModel(BaseModel):
    id: str
    key: str
    description: str
    value: float
    unit: str
    display: str
    when: str | None = None


class FactSheetModel(BaseModel):
    meter_id: str
    first_reading: str
    last_reading: str
    facts: list[FactModel]
    warnings: list[str]
    prompt_text: str

    @classmethod
    def from_fact_sheet(cls, sheet: FactSheet) -> FactSheetModel:
        return cls(
            meter_id=sheet.meter_id,
            first_reading=sheet.first_reading.isoformat(),
            last_reading=sheet.last_reading.isoformat(),
            facts=[
                FactModel(
                    id=f.id,
                    key=f.key,
                    description=f.description,
                    value=f.value,
                    unit=f.unit,
                    display=f.display,
                    when=f.when.isoformat() if f.when is not None else None,
                )
                for f in sheet.facts
            ],
            warnings=list(sheet.warnings),
            prompt_text=sheet.to_prompt_text(),
        )

    def to_fact_sheet(self) -> FactSheet:
        def moment(text: str | None) -> date | datetime | None:
            if text is None:
                return None
            return datetime.fromisoformat(text) if "T" in text else date.fromisoformat(text)

        return FactSheet(
            meter_id=self.meter_id,
            first_reading=datetime.fromisoformat(self.first_reading),
            last_reading=datetime.fromisoformat(self.last_reading),
            facts=tuple(
                Fact(f.id, f.key, f.description, f.value, f.unit, moment(f.when))
                for f in self.facts
            ),
            warnings=tuple(self.warnings),
        )


class SavingsSheetModel(FactSheetModel):
    """A fact sheet of costs and estimated savings, with the words that frame them."""

    preface: str

    @classmethod
    def from_savings_sheet(cls, sheet: SavingsSheet) -> SavingsSheetModel:
        base = FactSheetModel.from_fact_sheet(sheet)
        return cls(**base.model_dump(), preface=sheet.preface)

    def to_savings_sheet(self) -> SavingsSheet:
        base = self.to_fact_sheet()
        return SavingsSheet(
            meter_id=base.meter_id,
            first_reading=base.first_reading,
            last_reading=base.last_reading,
            facts=base.facts,
            warnings=base.warnings,
            preface=self.preface,
        )


class SiteList(BaseModel):
    sites: list[str]
    default_site: str


class AnomalyModel(BaseModel):
    id: str
    kind: str
    start: str
    end: str
    intervals: int
    peak_at: str
    observed_kw_at_peak: float
    baseline_kw_at_peak: float
    peak_deviation_kw: float
    energy_difference_kwh: float


class AnomalyListModel(BaseModel):
    meter_id: str
    anomalies: list[AnomalyModel]
    judged_readings: int
    unjudged_readings: int
    note: str

    @classmethod
    def from_result(cls, result: DetectionResult) -> AnomalyListModel:
        return cls(
            meter_id=result.meter_id,
            anomalies=[
                AnomalyModel(
                    id=a.id,
                    kind=a.kind,
                    start=a.start.isoformat(),
                    end=a.end.isoformat(),
                    intervals=a.intervals,
                    peak_at=a.peak_at.isoformat(),
                    observed_kw_at_peak=round(a.observed_kw_at_peak, 3),
                    baseline_kw_at_peak=round(a.baseline_kw_at_peak, 3),
                    peak_deviation_kw=round(a.peak_deviation_kw, 3),
                    energy_difference_kwh=round(a.energy_difference_kwh, 3),
                )
                for a in result.anomalies
            ],
            judged_readings=result.judged_readings,
            unjudged_readings=result.unjudged_readings,
            note=(
                "Unusual readings compared with the same time of day on other days of the same "
                "kind. These are events to look into, not faults. Readings without enough "
                "history are counted as unjudged."
            ),
        )
