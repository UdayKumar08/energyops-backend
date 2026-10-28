"""Where the approval graph's saved state lives: LangGraph's own PostgreSQL tables.

The tables are created by LangGraph's saver, once, by the owner role (the application role owns
nothing and cannot create tables). `setup_checkpointer` is called by the migration step. At run
time the service connects as the application role through its own small connection pool.

These tables are not under row level security: the saver has no organisation column. Access is
controlled in code instead, because a graph is only ever resumed for a ticket id that the caller
first read through the tickets table, which is under row level security.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

# The tables LangGraph's saver creates for itself. Migrations leave them alone.
CHECKPOINT_TABLES = frozenset(
    {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}
)


def plain_url(url: str) -> str:
    """psycopg wants postgresql://, SQLAlchemy's URL says postgresql+psycopg://."""
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


async def setup_checkpointer(admin_url: str) -> None:
    """Create or update LangGraph's tables, as the owner role."""
    async with AsyncPostgresSaver.from_conn_string(plain_url(admin_url)) as saver:
        await saver.setup()


_CONNECTION_OPTIONS: dict[str, Any] = {
    "autocommit": True,
    "prepare_threshold": 0,
    "row_factory": dict_row,
}


class Checkpointing:
    """A lazily opened connection pool and the saver that uses it."""

    def __init__(self, url: str) -> None:
        self._url = plain_url(url)
        self._pool: AsyncConnectionPool[AsyncConnection[DictRow]] | None = None
        self._saver: AsyncPostgresSaver | None = None

    async def saver(self) -> AsyncPostgresSaver:
        if self._saver is None:
            pool: AsyncConnectionPool[AsyncConnection[DictRow]] = AsyncConnectionPool(
                self._url,
                min_size=1,
                max_size=4,
                open=False,
                kwargs=_CONNECTION_OPTIONS,
            )
            await pool.open()
            self._pool = pool
            self._saver = AsyncPostgresSaver(pool)
        return self._saver

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
        self._pool = None
        self._saver = None
