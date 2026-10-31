"""The one-time database preparation, run against a real PostgreSQL as a throwaway database
and throwaway roles (role names are shared across a whole server, so they are made unique)."""

from __future__ import annotations

import os
import secrets
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql

from energyops.db.bootstrap import BootstrapError, bootstrap

SUPERUSER = os.environ.get(
    "ENERGYOPS_TEST_SUPERUSER_URL", "postgresql://energyops:energyops@127.0.0.1:5433/postgres"
)

pytestmark = pytest.mark.db


def _swap_database(url: str, name: str) -> str:
    return url.rsplit("/", 1)[0] + "/" + name


@pytest.fixture
def scratch() -> Iterator[tuple[str, str, str, str]]:
    try:
        admin = psycopg.connect(SUPERUSER, autocommit=True, connect_timeout=3)
    except psycopg.Error as exc:
        if os.environ.get("ENERGYOPS_REQUIRE_DB"):
            raise
        pytest.skip(f"no superuser connection: {exc}")
    suffix = secrets.token_hex(4)
    database, owner, app = f"eo_boot_{suffix}", f"eo_boot_owner_{suffix}", f"eo_boot_app_{suffix}"
    admin.execute(sql.SQL("create database {}").format(sql.Identifier(database)))
    try:
        yield _swap_database(SUPERUSER, database), database, owner, app
    finally:
        admin.execute(
            sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(database))
        )
        for role in (owner, app):
            admin.execute(sql.SQL("drop role if exists {}").format(sql.Identifier(role)))
        admin.close()


def _url(base: str, role: str, password: str) -> str:
    rest = base.split("@", 1)[1]
    return f"postgresql://{role}:{password}@{rest}"


def test_it_creates_roles_that_can_and_cannot_do_the_right_things(scratch) -> None:
    url, database, owner, app = scratch
    bootstrap(url, owner_password="o" * 24, app_password="a" * 24, owner_role=owner, app_role=app)

    with psycopg.connect(_url(url, owner, "o" * 24), autocommit=True) as conn:
        conn.execute("create table t (id int)")  # the owner can change the schema
        conn.execute(sql.SQL("grant select on t to {}").format(sql.Identifier(app)))
        conn.execute("insert into t values (1)")
    with psycopg.connect(_url(url, app, "a" * 24), autocommit=True) as conn:
        assert conn.execute("select count(*) from t").fetchone() == (1,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("drop table t")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("create table x (id int)")
    with psycopg.connect(url) as conn:
        assert conn.execute("select 1 from pg_extension where extname = 'vector'").fetchone()
        flags = conn.execute(
            "select rolsuper, rolcreatedb, rolcreaterole from pg_roles where rolname = %s", (app,)
        ).fetchone()
        assert flags == (False, False, False)
        assert conn.execute(
            "select datname from pg_database where datname = %s and datdba = "
            "(select oid from pg_roles where rolname = %s)",
            (database, owner),
        ).fetchone()


def test_running_it_again_resets_the_passwords(scratch) -> None:
    # The stored hash is compared rather than trying the old password: a local test server may
    # let local connections in without checking passwords at all.
    url, _, owner, app = scratch

    def stored_hash() -> str:
        with psycopg.connect(url) as conn:
            row = conn.execute(
                "select rolpassword from pg_authid where rolname = %s", (app,)
            ).fetchone()
        assert row is not None
        return str(row[0])

    bootstrap(url, owner_password="o" * 24, app_password="a" * 24, owner_role=owner, app_role=app)
    first = stored_hash()
    bootstrap(url, owner_password="p" * 24, app_password="b" * 24, owner_role=owner, app_role=app)
    assert stored_hash() != first
    psycopg.connect(_url(url, app, "b" * 24)).close()


def test_a_database_that_other_roles_could_reach_is_closed_to_them(scratch) -> None:
    url, database, owner, app = scratch
    bootstrap(url, owner_password="o" * 24, app_password="a" * 24, owner_role=owner, app_role=app)
    with psycopg.connect(url) as conn:
        grants = conn.execute(
            "select has_database_privilege('public', %s, 'connect')", (database,)
        ).fetchone()
        assert grants == (False,)


def test_short_passwords_and_equal_roles_are_refused(scratch) -> None:
    url, _, owner, app = scratch
    with pytest.raises(BootstrapError, match="at least"):
        bootstrap(
            url, owner_password="short", app_password="a" * 24, owner_role=owner, app_role=app
        )
    with pytest.raises(BootstrapError, match="different"):
        bootstrap(
            url, owner_password="o" * 24, app_password="a" * 24, owner_role=owner, app_role=owner
        )


def test_an_unreachable_server_is_reported_without_the_password() -> None:
    with pytest.raises(BootstrapError) as info:
        bootstrap(
            "postgresql://admin:supersecretvalue1234567@127.0.0.1:1/db?connect_timeout=2",
            owner_password="o" * 24,
            app_password="a" * 24,
        )
    assert "supersecretvalue" not in str(info.value)
    assert "o" * 24 not in str(info.value)


def test_the_owner_can_create_tables_where_the_public_schema_is_locked_down(scratch) -> None:
    # Azure's Postgres gives the public schema to its administrator group and lets no one
    # else create tables in it; this recreates that before running the bootstrap.
    url, _, owner, app = scratch
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("alter schema public owner to current_user")
        conn.execute("revoke all on schema public from public")
    bootstrap(url, owner_password="o" * 24, app_password="a" * 24, owner_role=owner, app_role=app)
    with psycopg.connect(_url(url, owner, "o" * 24), autocommit=True) as conn:
        conn.execute("create table t (id int)")
