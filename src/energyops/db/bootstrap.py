"""Create the two database roles and the extension a new database needs, once.

    ENERGYOPS_BOOTSTRAP_ADMIN_URL=postgresql://<server admin>:...@<host>/<database>
    ENERGYOPS_BOOTSTRAP_OWNER_PASSWORD=...  ENERGYOPS_BOOTSTRAP_APP_PASSWORD=...
    python -m energyops.db.bootstrap

The server administrator is the only account that can do this, and it is used here and nowhere
else. The owner role owns the database and runs migrations. The application role owns nothing,
cannot change the schema, and is what the running API connects as, so row level security applies
to it. Running this again resets both passwords to the values given and changes nothing else, so
a rotated password can be applied the same way.

The `vector` extension must already be allowed on the server (Azure's `azure.extensions`
setting); this creates it and says so plainly if the server refuses.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

import psycopg
from psycopg import sql

MIN_PASSWORD_LENGTH = 20
OWNER_ROLE = "energyops_owner"
APP_ROLE = "energyops_app"


class BootstrapError(RuntimeError):
    """The database could not be prepared. The message says what to fix."""


def _check_password(label: str, value: str) -> None:
    if len(value) < MIN_PASSWORD_LENGTH:
        raise BootstrapError(
            f"The {label} password must be at least {MIN_PASSWORD_LENGTH} characters."
        )


def _database_of(admin_url: str) -> str:
    name = urlparse(admin_url).path.lstrip("/")
    if not name:
        raise BootstrapError("The admin URL must name the database, e.g. .../energyops")
    return name


def _set_role(
    conn: psycopg.Connection, role: str, password: str, *, create_db_owner: bool = False
) -> None:
    exists = conn.execute("select 1 from pg_roles where rolname = %s", (role,)).fetchone()
    verb = "alter" if exists else "create"
    conn.execute(
        sql.SQL(
            "{} role {} login nosuperuser nocreatedb nocreaterole noinherit password {}"
        ).format(sql.SQL(verb), sql.Identifier(role), sql.Literal(password))
    )


def bootstrap(
    admin_url: str,
    *,
    owner_password: str,
    app_password: str,
    owner_role: str = OWNER_ROLE,
    app_role: str = APP_ROLE,
) -> None:
    _check_password("owner", owner_password)
    _check_password("application", app_password)
    if owner_role == app_role:
        raise BootstrapError("The owner and application roles must be different roles.")
    database = _database_of(admin_url)
    try:
        with psycopg.connect(admin_url.replace("postgresql+psycopg://", "postgresql://")) as conn:
            conn.autocommit = True
            _set_role(conn, owner_role, owner_password)
            _set_role(conn, app_role, app_password)
            db = sql.Identifier(database)
            # A server administrator that is not a superuser may only hand a database to a role
            # it belongs to.
            conn.execute(sql.SQL("grant {} to current_user").format(sql.Identifier(owner_role)))
            conn.execute(
                sql.SQL("alter database {} owner to {}").format(db, sql.Identifier(owner_role))
            )
            conn.execute(sql.SQL("revoke all on database {} from public").format(db))
            conn.execute(
                sql.SQL("grant connect on database {} to {}").format(db, sql.Identifier(app_role))
            )
            try:
                conn.execute("create extension if not exists vector")
            except psycopg.Error as exc:
                raise BootstrapError(
                    "The server would not create the 'vector' extension. On Azure, add VECTOR to "
                    f"the azure.extensions server parameter first. ({exc.diag.message_primary})"
                ) from None
    except psycopg.OperationalError as exc:
        raise BootstrapError(f"Could not connect as the server administrator: {exc}") from None
    except psycopg.Error as exc:
        raise BootstrapError(f"The database refused a step: {exc.diag.message_primary}") from None


def main() -> None:
    try:
        url = os.environ.get("ENERGYOPS_BOOTSTRAP_ADMIN_URL")
        owner_password = os.environ.get("ENERGYOPS_BOOTSTRAP_OWNER_PASSWORD")
        app_password = os.environ.get("ENERGYOPS_BOOTSTRAP_APP_PASSWORD")
        if not (url and owner_password and app_password):
            raise BootstrapError(
                "Set ENERGYOPS_BOOTSTRAP_ADMIN_URL, ENERGYOPS_BOOTSTRAP_OWNER_PASSWORD and "
                "ENERGYOPS_BOOTSTRAP_APP_PASSWORD."
            )
        bootstrap(
            url,
            owner_password=owner_password,
            app_password=app_password,
            owner_role=os.environ.get("ENERGYOPS_DB_OWNER_ROLE", OWNER_ROLE),
            app_role=os.environ.get("ENERGYOPS_DB_APP_ROLE", APP_ROLE),
        )
    except BootstrapError as exc:
        raise SystemExit(f"Bootstrap failed: {exc}") from None
    print("The database roles and extension are in place.")


if __name__ == "__main__":
    main()
