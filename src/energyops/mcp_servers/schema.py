"""The shape a fact sheet has when it travels over MCP, and the way back to a FactSheet.

Everything is plain JSON types, so a client in any language can read it. `to_fact_sheet` rebuilds
the exact object the server computed, which is what lets an agent use a fact sheet it fetched
through a tool with the same grounding check it uses for one it computed itself.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel

from energyops.facts import Fact, FactSheet


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


class SiteList(BaseModel):
    sites: list[str]
    default_site: str
