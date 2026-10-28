"""Database sessions that know which organisation they act for.

Every session is opened for a `Scope`. The scope is written into the transaction as two settings
that the row level security policies read, so Postgres itself refuses to show or change another
organisation's rows. The scope must come from the signed-in person, never from a model or a tool.

A session is one transaction: it commits when the block ends and rolls back if it raises. The
settings last only for that transaction, so code inside the block must not commit by itself.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


@dataclass(frozen=True, slots=True)
class Scope:
    """Who a session acts for. `platform` is the product's own staff: it lifts the organisation
    filter on the few tables meant to be read across organisations (organisations, users, the
    audit log) and nothing else."""

    org_id: uuid.UUID | None = None
    platform: bool = False

    @classmethod
    def for_org(cls, org_id: uuid.UUID) -> Scope:
        return cls(org_id=org_id)

    @classmethod
    def platform_wide(cls) -> Scope:
        return cls(platform=True)


class Database:
    def __init__(self, url: str, *, pool_size: int = 5, echo: bool = False) -> None:
        self._engine: AsyncEngine = create_async_engine(
            url, pool_size=pool_size, pool_pre_ping=True, echo=echo
        )
        self._sessions = async_sessionmaker(self._engine, expire_on_commit=False)

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @asynccontextmanager
    async def session(self, scope: Scope) -> AsyncIterator[AsyncSession]:
        async with self._sessions() as session, session.begin():
            await session.execute(
                text(
                    "select set_config('app.org_id', :org, true), "
                    "set_config('app.platform', :p, true)"
                ),
                {
                    "org": str(scope.org_id) if scope.org_id else "",
                    "p": "on" if scope.platform else "",
                },
            )
            yield session

    async def dispose(self) -> None:
        await self._engine.dispose()
