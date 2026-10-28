"""Shared fixtures. Tests marked `db` need the test PostgreSQL (docker compose up -d db).

They connect as two roles, like the product does: the owner runs migrations and cleans up between
tests, and the restricted application role is what the code under test uses, so row level
security applies to it exactly as it will in a deployment. Set ENERGYOPS_REQUIRE_DB=1 (CI does) to
make a missing database a failure instead of a skip.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import httpx
import psycopg
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config

from energyops.db.session import Database

OWNER_URL = os.environ.get(
    "ENERGYOPS_TEST_OWNER_URL",
    "postgresql+psycopg://energyops_owner:energyops_owner@127.0.0.1:5433/energyops_test",
)
APP_URL = os.environ.get(
    "ENERGYOPS_TEST_APP_URL",
    "postgresql+psycopg://energyops_app:energyops_app@127.0.0.1:5433/energyops_test",
)


def _plain(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


def _reachable() -> str | None:
    """None when the test database answers, otherwise the reason it does not."""
    try:
        with psycopg.connect(_plain(OWNER_URL), connect_timeout=3) as conn:
            conn.execute("select 1")
        return None
    except psycopg.Error as exc:
        return str(exc).strip().splitlines()[0]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "migrated_db" in getattr(item, "fixturenames", ()) or "db" in getattr(
            item, "fixturenames", ()
        ):
            item.add_marker(pytest.mark.db)


@pytest.fixture(scope="session")
def migrated_db() -> str:
    """Empty the test database, run every migration as the owner and return the owner's URL."""
    reason = _reachable()
    if reason is not None:
        if os.environ.get("ENERGYOPS_REQUIRE_DB") == "1":
            pytest.fail(f"The test database is required but unreachable: {reason}")
        pytest.skip(f"No test database ({reason}). Start it with: docker compose up -d db")
    with psycopg.connect(_plain(OWNER_URL), autocommit=True) as conn:
        # Drop everything the owner created (tables, functions, policies, grants) but not the
        # vector extension, which a privileged role created beforehand and the owner cannot redo.
        conn.execute("drop owned by current_user cascade")
    config = Config("alembic.ini")
    config.attributes["url"] = OWNER_URL
    command.upgrade(config, "head")
    return OWNER_URL


def _truncate_everything() -> None:
    with psycopg.connect(_plain(OWNER_URL), autocommit=True) as conn:
        names = [
            row[0]
            for row in conn.execute(
                "select tablename from pg_tables where schemaname = 'public' "
                "and tablename <> 'alembic_version'"
            )
        ]
        if names:
            conn.execute("truncate " + ", ".join(f'"{n}"' for n in names) + " cascade")


@pytest_asyncio.fixture
async def db(migrated_db: str) -> AsyncIterator[Database]:
    """A Database connected as the restricted application role, on empty tables."""
    _truncate_everything()
    database = Database(APP_URL)
    try:
        yield database
    finally:
        await database.dispose()


@pytest_asyncio.fixture
async def owner_db(migrated_db: str) -> AsyncIterator[Database]:
    """A Database connected as the owner. Used to set up data; row level security does not
    apply to the owner, so tests of isolation never use this one."""
    database = Database(OWNER_URL)
    try:
        yield database
    finally:
        await database.dispose()


@pytest_asyncio.fixture
async def seeded(db: Database) -> Database:
    """The test database with the invented demo organisations, people, readings and tariffs."""
    from energyops.db.seed import seed_demo

    await seed_demo(db)
    return db


@pytest_asyncio.fixture
async def api(seeded: Database) -> AsyncIterator[httpx.AsyncClient]:
    """A client for the platform API, talking to the app in process."""
    from service_helpers import make_app

    transport = httpx.ASGITransport(app=make_app(seeded))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
