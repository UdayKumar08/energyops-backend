"""Alembic environment. Runs migrations as the owner role, never as the application's role."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from energyops.db import models  # noqa: F401  (registers the tables on the metadata)
from energyops.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def database_url() -> str:
    url = (
        context.get_x_argument(as_dictionary=True).get("url")
        or config.attributes.get("url")
        or os.environ.get("ENERGYOPS_ADMIN_DATABASE_URL")
    )
    if not url:
        raise SystemExit(
            "No database URL. Set ENERGYOPS_ADMIN_DATABASE_URL to the owner role's URL "
            "(postgresql+psycopg://...)."
        )
    return str(url)


def run_migrations_online() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            # Hand-written statements (policies, functions, grants) are not tables or columns.
            include_object=lambda obj, name, type_, reflected, compare_to: (
                not (type_ == "table" and name in {"alembic_version"})
            ),
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline migrations are not supported: run against a database.")
run_migrations_online()
