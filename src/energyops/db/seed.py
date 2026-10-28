"""Demo data for a local database. Everything here is invented.

Two fictional organisations, so that tenant isolation can be seen working, with a few people in
each, the demo sites, 28 days of invented readings and the invented demo tariffs. Running it twice
changes nothing the second time. It refuses to run anywhere but the local environment.

    export ENERGYOPS_ADMIN_DATABASE_URL=postgresql+psycopg://energyops_owner:...@127.0.0.1:5433/energyops_dev
    uv run python -m energyops.db.seed
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from energyops.agents.sources import DEMO_TARIFFS, synthetic_demo_source
from energyops.config import Environment
from energyops.db import repo
from energyops.db.models import Organization
from energyops.db.session import Database, Scope

DEMO_DAYS = 28
PLATFORM_EMAIL = "platform@energyops.example"


@dataclass(frozen=True, slots=True)
class DemoPerson:
    email: str
    name: str
    role: str
    only_sites: tuple[str, ...] | None = None  # slugs; None means every site


@dataclass(frozen=True, slots=True)
class DemoOrg:
    name: str
    slug: str
    sites: tuple[tuple[str, str], ...]  # (slug, name)
    people: tuple[DemoPerson, ...]


DEMO_ORGS = (
    DemoOrg(
        "Harbour Hotels",
        "harbour-hotels",
        (("demo-hotel", "Harbour Hotel"), ("demo-office", "Harbour Head Office")),
        (
            DemoPerson("admin@harbour.example", "Hana Harbour", "org_admin"),
            DemoPerson("manager@harbour.example", "Marco Harbour", "manager"),
            DemoPerson("tech@harbour.example", "Tess Harbour", "technician", ("demo-hotel",)),
            DemoPerson("viewer@harbour.example", "Vik Harbour", "viewer"),
        ),
    ),
    DemoOrg(
        "Northside Health",
        "northside-health",
        (("demo-clinic", "Northside Clinic"),),
        (
            DemoPerson("admin@northside.example", "Nia Northside", "org_admin"),
            DemoPerson("manager@northside.example", "Noor Northside", "manager"),
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class SeedResult:
    created: bool
    organizations: dict[str, uuid.UUID]


async def seed_demo(db: Database) -> SeedResult:
    """Create the demo organisations, people, sites, readings and tariffs if they are missing."""
    readings = synthetic_demo_source(days=DEMO_DAYS)
    created = False
    ids: dict[str, uuid.UUID] = {}

    async with db.session(Scope.platform_wide()) as session:
        if await repo.find_user(session, email=PLATFORM_EMAIL) is None:
            await repo.create_user(
                session,
                org_id=None,
                email=PLATFORM_EMAIL,
                display_name="Pat Platform",
                role="platform_admin",
            )
            created = True
        existing = {
            org.slug: org.id for org in (await session.execute(select(Organization))).scalars()
        }

    for demo in DEMO_ORGS:
        if demo.slug in existing:
            ids[demo.slug] = existing[demo.slug]
            continue
        created = True
        async with db.session(Scope.platform_wide()) as session:
            org = await repo.create_organization(session, demo.name, demo.slug)
        ids[demo.slug] = org.id

        site_ids: dict[str, uuid.UUID] = {}
        async with db.session(Scope.for_org(org.id)) as session:
            for slug, name in demo.sites:
                site, meter = await repo.create_site(session, org.id, slug, name)
                site_ids[slug] = site.id
                await repo.upsert_readings(session, org.id, meter.id, readings(slug))
                await repo.set_tariff(session, org.id, site.id, DEMO_TARIFFS[slug])
        # People are created in their own organisation's scope, as an organisation's admin would.
        async with db.session(Scope.for_org(org.id)) as session:
            for person in demo.people:
                await repo.create_user(
                    session,
                    org_id=org.id,
                    email=person.email,
                    display_name=person.name,
                    role=person.role,
                    site_ids=(
                        None
                        if person.only_sites is None
                        else [site_ids[slug] for slug in person.only_sites]
                    ),
                )
    return SeedResult(created=created, organizations=ids)


def main() -> None:
    if os.environ.get("ENERGYOPS_ENVIRONMENT", Environment.LOCAL.value) != Environment.LOCAL.value:
        raise SystemExit("The demo seed only runs in the local environment.")
    url = os.environ.get("ENERGYOPS_ADMIN_DATABASE_URL")
    if not url:
        raise SystemExit("Set ENERGYOPS_ADMIN_DATABASE_URL to the owner role's database URL.")

    async def go() -> SeedResult:
        db = Database(url)
        try:
            return await seed_demo(db)
        finally:
            await db.dispose()

    result = asyncio.run(go())
    print("Seeded the demo data." if result.created else "The demo data was already there.")


if __name__ == "__main__":
    main()
