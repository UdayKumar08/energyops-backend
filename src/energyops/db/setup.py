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
    if args.seed:
        from energyops.db.seed import main as seed_main

        seed_main()


if __name__ == "__main__":
    main()
