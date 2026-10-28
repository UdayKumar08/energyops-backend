"""Read-only energy tools over MCP.

Run it as a local subprocess that speaks MCP over stdin and stdout:

    uv run python -m energyops.mcp_servers.energy

It has three tools and no way to change anything. They take at most a site name, never a file path
or a web address, they read only the built-in demo data, and every number they return is
computed by our own Python functions, never by a model. Nothing may be printed to stdout except
MCP messages, because stdout is the protocol channel.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from energyops.agents.sources import DEMO_SITES, ReadingsSource, synthetic_demo_source
from energyops.anomalies import detect_anomalies
from energyops.calculations import EnergyDataError
from energyops.facts import build_fact_sheet
from energyops.mcp_servers.schema import AnomalyListModel, FactSheetModel, SiteList

SERVER_NAME = "energyops-energy"

# Expected failures start with one of these codes and a colon, so a client can tell them apart.

# Everything here only reads, never writes, never reaches outside this process's own data.
READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)


def create_server(
    source: ReadingsSource | None = None, sites: Sequence[str] = DEMO_SITES
) -> MCPServer:
    readings = source or synthetic_demo_source()
    known = tuple(sites)
    server = MCPServer(
        SERVER_NAME,
        description="Read-only energy figures for demo sites, computed in Python.",
        instructions=(
            "Call list_sites to see the sites, then get_fact_sheet with a site id to get the "
            "computed figures for it, or get_anomalies for unusual-reading events. "
            "Quote figures exactly as given. "
            "Nothing here can change any data."
        ),
    )

    @server.tool(annotations=READ_ONLY)
    def list_sites() -> SiteList:
        """List the sites that have energy data, and which one is used when none is named."""
        return SiteList(sites=list(known), default_site=known[0])

    @server.tool(annotations=READ_ONLY)
    def get_fact_sheet(
        site: Annotated[
            str, Field(min_length=1, max_length=64, description="A site id from list_sites")
        ],
    ) -> FactSheetModel:
        """Get the computed fact sheet for one site: numbered facts, each with its exact display.

        The site is required on purpose. A misspelled argument name must fail loudly, not quietly
        return some other site's figures.
        """
        if site not in known:
            raise ToolError(
                f"unknown_site: Unknown site {site!r}. Call list_sites to see the valid sites."
            )
        try:
            return FactSheetModel.from_fact_sheet(build_fact_sheet(readings(site)))
        except EnergyDataError as exc:
            raise ToolError(
                f"unusable_data: The readings for {site!r} cannot be used: {exc}"
            ) from None

    @server.tool(annotations=READ_ONLY)
    def get_anomalies(
        site: Annotated[
            str, Field(min_length=1, max_length=64, description="A site id from list_sites")
        ],
    ) -> AnomalyListModel:
        """Get unusual-reading events for one site, found by a deterministic detector.

        An event is a run of readings far from what the same time of day looked like on other
        days of the same kind. It is something to look into, never a diagnosis.
        """
        if site not in known:
            raise ToolError(
                f"unknown_site: Unknown site {site!r}. Call list_sites to see the valid sites."
            )
        try:
            return AnomalyListModel.from_result(detect_anomalies(readings(site)))
        except EnergyDataError as exc:
            raise ToolError(
                f"unusable_data: The readings for {site!r} cannot be used: {exc}"
            ) from None

    return server


def main() -> None:
    create_server().run("stdio")


if __name__ == "__main__":
    main()
