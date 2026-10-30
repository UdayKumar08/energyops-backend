"""The first people in a new deployment: the platform admin who runs the product, and, outside
production, one invented customer so the screens have something to show.

It runs as part of `migrate`, as the owner role, and is safe to run again: whatever is already
there is left alone. Nothing here is a password. People sign in through Microsoft, and a person
only gets in if their email is in the users table.

    ENERGYOPS_FIRST_ADMIN_EMAIL           the platform admin's email (required to do anything)
    ENERGYOPS_FIRST_ADMIN_NAME            their display name (default: Platform admin)
    ENERGYOPS_FIRST_ADMIN_OBJECT_ID       their Microsoft Entra object id (optional). Sign-in then
                                          matches on it, which works whatever address Microsoft
                                          puts in the token.
    ENERGYOPS_DEMO_CUSTOMER_ADMIN_EMAIL   when set, create the demo customer with this admin
                                          (refused in production)
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from energyops.agents.sources import DEMO_TARIFFS, synthetic_demo_source
from energyops.config import Environment
from energyops.db import repo
from energyops.db.models import Organization
from energyops.db.session import Database, Scope

DEMO_ORG_NAME = "Demo Energy Co"
DEMO_ORG_SLUG = "demo-energy-co"
DEMO_SITE_SLUG = "demo-hotel"  # the invented readings and tariff for this slug are reused
DEMO_SITE_NAME = "Demo Site"
DEMO_DAYS = 28
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True, slots=True)
class FirstRunResult:
    platform_admin_created: bool
    demo_customer_created: bool


class FirstRunError(ValueError):
    """The settings are not usable. The message says why."""


def _clean_email(value: str, what: str) -> str:
    email = value.strip().lower()
    if not _EMAIL.match(email):
        raise FirstRunError(f"{what} is not an email address.")
    return email


def _clean_object_id(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    try:
        return str(uuid.UUID(value.strip()))
    except ValueError:
        raise FirstRunError("The platform admin object id is not a UUID.") from None


async def first_run(
    db: Database,
    *,
    environment: Environment,
    admin_email: str | None,
    admin_name: str = "Platform admin",
    admin_object_id: str | None = None,
    demo_customer_admin_email: str | None = None,
) -> FirstRunResult:
    platform_created = False
    demo_created = False
    if admin_email:
        email = _clean_email(admin_email, "The platform admin email")
        object_id = _clean_object_id(admin_object_id)
        async with db.session(Scope.platform_wide()) as session:
            user = await repo.find_user(session, email=email)
            if user is None:
                user = await repo.create_user(
                    session,
                    org_id=None,
                    email=email,
                    display_name=admin_name.strip() or "Platform admin",
                    role="platform_admin",
                )
                platform_created = True
            # Record the Microsoft id so sign-in finds this person by it. Never overwrite one.
            if object_id and user.external_id is None:
                user.external_id = object_id
    if demo_customer_admin_email:
        if environment is Environment.PROD:
            raise FirstRunError("The demo customer is never created in production.")
        demo_created = await _demo_customer(
            db, _clean_email(demo_customer_admin_email, "The demo customer admin email")
        )
    return FirstRunResult(platform_created, demo_created)


async def _demo_customer(db: Database, admin_email: str) -> bool:
    async with db.session(Scope.platform_wide()) as session:
        existing = (
            await session.execute(select(Organization).where(Organization.slug == DEMO_ORG_SLUG))
        ).scalar_one_or_none()
        if existing is not None:
            return False
        org = await repo.create_organization(session, DEMO_ORG_NAME, DEMO_ORG_SLUG)
    org_id: uuid.UUID = org.id
    readings = synthetic_demo_source(days=DEMO_DAYS)(DEMO_SITE_SLUG)
    async with db.session(Scope.for_org(org_id)) as session:
        site, meter = await repo.create_site(session, org_id, DEMO_SITE_SLUG, DEMO_SITE_NAME)
        await repo.upsert_readings(session, org_id, meter.id, readings)
        await repo.set_tariff(session, org_id, site.id, DEMO_TARIFFS[DEMO_SITE_SLUG])
        await repo.create_user(
            session,
            org_id=org_id,
            email=admin_email,
            display_name="Demo customer admin",
            role="org_admin",
        )
    return True
