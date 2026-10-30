"""Bring a database up to date: run every migration, create the approval graph's tables, and
optionally load the invented demo data (local environment only).

    export ENERGYOPS_ADMIN_DATABASE_URL=postgresql+psycopg://energyops_owner:...@127.0.0.1:5433/energyops_dev
    uv run python -m energyops.db.setup [--seed]

It runs as the owner role and is safe to run again. In the cloud it runs as a separate job
(the image's `migrate` command) so that the running API never holds the owner role's password.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from alembic import command
from alembic.config import Config

from energyops.config import Environment
from energyops.db.checkpoint import setup_checkpointer

MIGRATIONS = Path(__file__).parent / "migrations"


def migrate(admin_url: str) -> None:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.attributes["url"] = admin_url
    command.upgrade(config, "head")


def _first_run(url: str) -> None:
    """Create the first platform admin (and, outside production, the demo customer) when the
    environment variables name them. Safe to run again."""
    from energyops.db.first_run import FirstRunError, first_run
    from energyops.db.session import Database

    admin = os.environ.get("ENERGYOPS_FIRST_ADMIN_EMAIL", "").strip()
    demo = os.environ.get("ENERGYOPS_DEMO_CUSTOMER_ADMIN_EMAIL", "").strip()
    if not admin and not demo:
        return
    environment = Environment(os.environ.get("ENERGYOPS_ENVIRONMENT", Environment.LOCAL.value))

    async def go() -> None:
        db = Database(url)
        try:
            result = await first_run(
                db,
                environment=environment,
                admin_email=admin or None,
                admin_name=os.environ.get("ENERGYOPS_FIRST_ADMIN_NAME", "Platform admin"),
                demo_customer_admin_email=demo or None,
            )
        finally:
            await db.dispose()
        print(
            "Platform admin: " + ("created." if result.platform_admin_created else "already there.")
        )
        if demo:
            print(
                "Demo customer: "
                + ("created." if result.demo_customer_created else "already there.")
            )

    try:
        asyncio.run(go())
    except FirstRunError as exc:
        raise SystemExit(str(exc)) from None


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m energyops.db.setup", description=__doc__)
    parser.add_argument("--seed", action="store_true", help="load the demo data (local only)")
    args = parser.parse_args()
    url = os.environ.get("ENERGYOPS_ADMIN_DATABASE_URL")
    if not url:
        raise SystemExit("Set ENERGYOPS_ADMIN_DATABASE_URL to the owner role's database URL.")
    if args.seed and os.environ.get("ENERGYOPS_ENVIRONMENT", "local") != Environment.LOCAL.value:
        raise SystemExit("The demo seed only runs in the local environment.")

    migrate(url)
    asyncio.run(setup_checkpointer(url))
    print("The database is up to date.")
    _first_run(url)
    if args.seed:
        from energyops.db.seed import main as seed_main

        seed_main()


if __name__ == "__main__":
    main()
