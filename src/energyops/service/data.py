"""Loading one site's data for a signed-in person, and turning it into the sources an agent reads.

Everything here runs inside the caller's database scope and is filtered by their site access, so
an agent can only ever be handed data the caller could see on screen. The agent gets a source that
holds just that one site: asking it for any other name fails the same way an unknown name does,
and the MCP server built for the run lists only that site. Nothing the model writes can widen it.
"""

from __future__ import annotations

from dataclasses import dataclass

from energyops.agents.registry import DataSources
from energyops.db import repo
from energyops.db.models import Site
from energyops.db.session import Database
from energyops.mcp_servers.energy import create_server
from energyops.security.principal import Principal
from energyops.synthetic import MeterReading
from energyops.tariffs import Tariff

LOOKBACK_DAYS = 28


class SiteNotFound(LookupError):
    """No such site, or the person may not see it. The two look the same on purpose."""


@dataclass(frozen=True, slots=True)
class SiteData:
    site: Site
    readings: list[MeterReading]
    tariff: Tariff | None


async def load_site(
    db: Database, principal: Principal, slug: str | None, *, days: int = LOOKBACK_DAYS
) -> SiteData:
    """The named site (or the person's first site when none is named), with its latest readings
    and tariff. Raises SiteNotFound for a site that is missing or not theirs."""
    org = principal.org()
    async with db.session(principal.scope) as session:
        if slug is None:
            sites = await repo.list_sites(session, org, principal.site_ids)
            site = sites[0] if sites else None
        else:
            site = await repo.get_site(session, org, slug, principal.site_ids)
        if site is None:
            raise SiteNotFound(slug or "no sites")
        meter = await repo.primary_meter(session, org, site.id)
        readings = (
            await repo.load_readings(session, org, meter, label=site.slug, days=days)
            if meter is not None
            else []
        )
        tariff = await repo.get_tariff(session, org, site.id)
    return SiteData(site=site, readings=readings, tariff=tariff)


def sources_for(data: SiteData) -> DataSources:
    """Readings, tariffs and an in-process MCP server that know only this one site."""
    slug = data.site.slug

    def readings(site: str | None) -> list[MeterReading]:
        if site not in (None, slug):
            raise LookupError(f"unknown site {site!r}")
        return data.readings

    def tariffs(site: str | None) -> Tariff | None:
        if site not in (None, slug):
            raise LookupError(f"unknown site {site!r}")
        return data.tariff

    server = create_server(source=readings, sites=[slug], tariffs=tariffs)
    return DataSources(readings=readings, tariffs=tariffs, mcp_target=server)
