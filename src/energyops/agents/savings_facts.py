"""How the savings advisor gets its cost facts: in this process, or from the MCP server.

Either way the sheet comes from `build_savings_sheet`, which needs the site's readings and its
tariff. A site with no tariff is a normal, reported outcome (error kind `no_tariff`), never a
reason to assume a price.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import ValidationError

from energyops.agents.fact_sources import McpToolSource, ToolAccessError
from energyops.agents.runs import TraceRecorder
from energyops.agents.sources import ReadingsSource, TariffSource
from energyops.mcp_servers.schema import SavingsSheetModel
from energyops.savings import SavingsSheet, build_savings_sheet
from energyops.tariffs import require_tariff


class SavingsFactsProvider(Protocol):
    via: str

    async def __call__(self, site: str | None, trace: TraceRecorder) -> SavingsSheet: ...


class DirectSavingsFacts:
    via = "direct"

    def __init__(self, source: ReadingsSource, tariffs: TariffSource) -> None:
        self._source = source
        self._tariffs = tariffs

    async def __call__(self, site: str | None, trace: TraceRecorder) -> SavingsSheet:
        readings = self._source(site)  # an unknown site is reported before a missing tariff
        return build_savings_sheet(readings, require_tariff(self._tariffs(site), site))


class McpSavingsFacts(McpToolSource):
    async def __call__(self, site: str | None, trace: TraceRecorder) -> SavingsSheet:
        needed = self._guard(site, ["get_savings_facts"])
        kind, payload = await self._fetch(site, needed, trace, tools=("get_savings_facts",))
        if kind != "ok":
            self._raise_failure(kind, payload)
        try:
            return SavingsSheetModel.model_validate(payload["get_savings_facts"]).to_savings_sheet()
        except (ValidationError, ValueError):
            raise ToolAccessError(
                "the tool returned a savings sheet in an unexpected shape"
            ) from None
