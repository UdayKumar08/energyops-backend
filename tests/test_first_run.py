"""The first platform admin and the demo customer, on a real PostgreSQL."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from energyops.config import Environment
from energyops.db import repo
from energyops.db.first_run import (
    DEMO_ORG_SLUG,
    FirstRunError,
    first_run,
)
from energyops.db.models import Organization, Reading
from energyops.db.session import Database, Scope

pytestmark = pytest.mark.db


async def test_first_admin_is_created_once_and_has_no_organisation(db: Database) -> None:
    for expected in (True, False):
        result = await first_run(
            db,
            environment=Environment.PROD,
            admin_email=" Uday@Example.com ",
            admin_name="Uday",
        )
        assert result.platform_admin_created is expected
    async with db.session(Scope.platform_wide()) as session:
        user = await repo.find_user(session, email="uday@example.com")
    assert user is not None and user.role == "platform_admin" and user.org_id is None


async def test_the_object_id_is_recorded_once_and_is_what_sign_in_finds(db: Database) -> None:
    oid = "ad192e78-8b3b-4096-b335-1bdbec6bde15"
    for other in (oid, "11111111-1111-1111-1111-111111111111"):
        await first_run(
            db, environment=Environment.PROD, admin_email="a@example.com", admin_object_id=other
        )
    async with db.session(Scope.platform_wide()) as session:
        user = await repo.find_user(session, external_id=oid)
    assert user is not None and user.email == "a@example.com"  # the second id did not replace it


async def test_a_bad_object_id_is_refused(db: Database) -> None:
    with pytest.raises(FirstRunError, match="not a UUID"):
        await first_run(
            db, environment=Environment.PROD, admin_email="a@example.com", admin_object_id="nope"
        )


async def test_nothing_is_created_without_settings(db: Database) -> None:
    result = await first_run(db, environment=Environment.PROD, admin_email=None)
    assert not result.platform_admin_created and not result.demo_customer_created


async def test_the_demo_customer_has_a_site_readings_and_one_admin(db: Database) -> None:
    for expected in (True, False):
        result = await first_run(
            db,
            environment=Environment.DEV,
            admin_email="a@example.com",
            demo_customer_admin_email="admin@demo.example",
        )
        assert result.demo_customer_created is expected
    async with db.session(Scope.platform_wide()) as session:
        org = (
            await session.execute(select(Organization).where(Organization.slug == DEMO_ORG_SLUG))
        ).scalar_one()
        people = await repo.list_users(session, org.id)
    assert [(p.email, p.role) for p in people] == [("admin@demo.example", "org_admin")]
    async with db.session(Scope.for_org(org.id)) as session:
        assert (
            await session.execute(select(func.count()).select_from(Reading))
        ).scalar_one() > 1000


async def test_the_demo_customer_is_refused_in_production(db: Database) -> None:
    with pytest.raises(FirstRunError, match="never created in production"):
        await first_run(
            db,
            environment=Environment.PROD,
            admin_email=None,
            demo_customer_admin_email="admin@demo.example",
        )


@pytest.mark.parametrize("bad", ["nope", "a@b", "@x.com"])
async def test_a_bad_email_is_refused(db: Database, bad: str) -> None:
    with pytest.raises(FirstRunError, match="not an email"):
        await first_run(db, environment=Environment.DEV, admin_email=bad)
