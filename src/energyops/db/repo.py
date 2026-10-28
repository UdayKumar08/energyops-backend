"""Reading and writing the tables. Every function takes the organisation it acts for and puts it
in the query, so a mistake in the session's scope is caught here and a mistake here is caught by
row level security. Functions that return a site take `only`: the set of site ids the person may
see, or None for all of them."""

from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from energyops.agents.runs import RunRecord
from energyops.db.models import (
    AgentSetting,
    AuditEvent,
    Meter,
    Organization,
    Reading,
    Run,
    Site,
    User,
    UserSiteAccess,
)
from energyops.db.models import Tariff as TariffRow
from energyops.synthetic import MeterReading
from energyops.tariffs import Tariff, TariffBand, TariffError

INSERT_CHUNK = 5000


# ------------------------------------------------------------------ organisations and people


async def create_organization(session: AsyncSession, name: str, slug: str) -> Organization:
    org = Organization(name=name, slug=slug)
    session.add(org)
    await session.flush()
    return org


async def create_user(
    session: AsyncSession,
    *,
    org_id: uuid.UUID | None,
    email: str,
    display_name: str,
    role: str,
    site_ids: Sequence[uuid.UUID] | None = None,
) -> User:
    """Add a person. `site_ids` limits them to those sites; None lets them see every site."""
    user = User(
        org_id=org_id,
        email=email.strip(),
        display_name=display_name,
        role=role,
        all_sites=site_ids is None,
    )
    session.add(user)
    await session.flush()
    for site_id in site_ids or ():
        assert org_id is not None
        session.add(UserSiteAccess(user_id=user.id, site_id=site_id, org_id=org_id))
    await session.flush()
    return user


async def find_user(
    session: AsyncSession, *, email: str | None = None, external_id: str | None = None
) -> User | None:
    """Find a person to sign in. Needs a platform scoped session, because it runs before the
    person's organisation is known."""
    if external_id is not None:
        found = (
            await session.execute(select(User).where(User.external_id == external_id))
        ).scalar_one_or_none()
        if found is not None:
            return found
    if email is not None:
        return (
            await session.execute(
                select(User).where(func.lower(User.email) == email.strip().lower())
            )
        ).scalar_one_or_none()
    return None


async def get_organization(session: AsyncSession, org_id: uuid.UUID) -> Organization | None:
    return (
        await session.execute(select(Organization).where(Organization.id == org_id))
    ).scalar_one_or_none()


async def site_ids_for(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID
) -> frozenset[uuid.UUID]:
    """The sites a person limited to some sites may see. Read inside their own organisation."""
    rows = await session.execute(
        select(UserSiteAccess.site_id).where(
            UserSiteAccess.user_id == user_id, UserSiteAccess.org_id == org_id
        )
    )
    return frozenset(rows.scalars())


async def allowed_site_ids(session: AsyncSession, user: User) -> frozenset[uuid.UUID] | None:
    """The sites a person may see, or None for every site of their organisation."""
    if user.all_sites or user.org_id is None:
        return None
    return await site_ids_for(session, user.org_id, user.id)


# ------------------------------------------------------------------ sites and meters


async def create_site(
    session: AsyncSession, org_id: uuid.UUID, slug: str, name: str, *, interval_minutes: int = 15
) -> tuple[Site, Meter]:
    site = Site(org_id=org_id, slug=slug, name=name)
    session.add(site)
    await session.flush()
    meter = Meter(
        org_id=org_id,
        site_id=site.id,
        code=slug,
        interval_minutes=interval_minutes,
        is_primary=True,
    )
    session.add(meter)
    await session.flush()
    return site, meter


async def list_sites(
    session: AsyncSession, org_id: uuid.UUID, only: frozenset[uuid.UUID] | None
) -> list[Site]:
    query = select(Site).where(Site.org_id == org_id).order_by(Site.name)
    if only is not None:
        query = query.where(Site.id.in_(only))
    return list((await session.execute(query)).scalars())


async def get_site(
    session: AsyncSession, org_id: uuid.UUID, slug: str, only: frozenset[uuid.UUID] | None
) -> Site | None:
    """A site by its slug, or None when it does not exist or the person may not see it. The two
    cases look the same on purpose, so a person cannot probe for sites they are not allowed."""
    query = select(Site).where(Site.org_id == org_id, Site.slug == slug)
    if only is not None:
        query = query.where(Site.id.in_(only))
    return (await session.execute(query)).scalar_one_or_none()


async def primary_meter(
    session: AsyncSession, org_id: uuid.UUID, site_id: uuid.UUID
) -> Meter | None:
    return (
        await session.execute(
            select(Meter).where(
                Meter.org_id == org_id, Meter.site_id == site_id, Meter.is_primary.is_(True)
            )
        )
    ).scalar_one_or_none()


# ------------------------------------------------------------------ readings


async def upsert_readings(
    session: AsyncSession, org_id: uuid.UUID, meter_id: uuid.UUID, readings: Sequence[MeterReading]
) -> int:
    """Store readings, replacing any already stored for the same meter and time."""
    total = 0
    for start in range(0, len(readings), INSERT_CHUNK):
        chunk = readings[start : start + INSERT_CHUNK]
        rows = [
            {"meter_id": meter_id, "ts": r.timestamp, "power_kw": r.power_kw, "org_id": org_id}
            for r in chunk
        ]
        statement = insert(Reading).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=[Reading.meter_id, Reading.ts],
            set_={"power_kw": statement.excluded.power_kw},
        )
        await session.execute(statement)
        total += len(chunk)
    return total


async def reading_range(
    session: AsyncSession, org_id: uuid.UUID, meter_id: uuid.UUID
) -> tuple[datetime, datetime] | None:
    row = (
        await session.execute(
            select(func.min(Reading.ts), func.max(Reading.ts)).where(
                Reading.org_id == org_id, Reading.meter_id == meter_id
            )
        )
    ).one()
    return (row[0], row[1]) if row[0] is not None else None


async def load_readings(
    session: AsyncSession,
    org_id: uuid.UUID,
    meter: Meter,
    *,
    label: str,
    days: int,
) -> list[MeterReading]:
    """The last `days` UTC days of a meter's readings, ending at its latest reading. `label` is
    the meter id the readings carry (the site's slug, which is what the agents name it)."""
    span = await reading_range(session, org_id, meter.id)
    if span is None:
        return []
    first_day = (span[1].astimezone(UTC) - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    rows = await session.execute(
        select(Reading.ts, Reading.power_kw)
        .where(Reading.org_id == org_id, Reading.meter_id == meter.id, Reading.ts >= first_day)
        .order_by(Reading.ts)
    )
    return [
        MeterReading(label, ts.astimezone(UTC), meter.interval_minutes, power) for ts, power in rows
    ]


# ------------------------------------------------------------------ tariffs


def tariff_from_row(row: TariffRow) -> Tariff:
    """Build a Tariff from its stored form. Raises TariffError if the stored data is unusable."""
    try:
        bands = tuple(TariffBand(**band) for band in row.bands)
        return Tariff(
            label=row.label,
            currency=row.currency,  # type: ignore[arg-type]
            price_per_kwh=row.price_per_kwh,
            bands=bands,
            demand_charge_per_kw_month=row.demand_charge_per_kw_month,
            is_demo=row.is_demo,
        )
    except TypeError as exc:
        raise TariffError(f"the stored bands are not usable: {exc}") from None


def bands_to_json(tariff: Tariff) -> list[dict[str, Any]]:
    return [
        {
            "name": b.name,
            "start_hour": b.start_hour,
            "end_hour": b.end_hour,
            "price_per_kwh": b.price_per_kwh,
            "days": b.days,
        }
        for b in tariff.bands
    ]


async def get_tariff(session: AsyncSession, org_id: uuid.UUID, site_id: uuid.UUID) -> Tariff | None:
    row = (
        await session.execute(
            select(TariffRow).where(TariffRow.org_id == org_id, TariffRow.site_id == site_id)
        )
    ).scalar_one_or_none()
    return tariff_from_row(row) if row is not None else None


async def set_tariff(
    session: AsyncSession, org_id: uuid.UUID, site_id: uuid.UUID, tariff: Tariff
) -> None:
    values = {
        "org_id": org_id,
        "site_id": site_id,
        "label": tariff.label,
        "currency": tariff.currency,
        "price_per_kwh": tariff.price_per_kwh,
        "bands": bands_to_json(tariff),
        "demand_charge_per_kw_month": tariff.demand_charge_per_kw_month,
        "is_demo": tariff.is_demo,
        "updated_at": func.now(),
    }
    statement = insert(TariffRow).values(values)
    statement = statement.on_conflict_do_update(
        index_elements=[TariffRow.site_id],
        set_={k: v for k, v in values.items() if k not in ("org_id", "site_id")},
    )
    await session.execute(statement)


async def remove_tariff(session: AsyncSession, org_id: uuid.UUID, site_id: uuid.UUID) -> None:
    await session.execute(
        delete(TariffRow).where(TariffRow.org_id == org_id, TariffRow.site_id == site_id)
    )


# ------------------------------------------------------------------ runs


async def save_run(
    session: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    agent_id: str,
    record: RunRecord,
) -> None:
    session.add(
        Run(
            id=uuid.UUID(record.run_id),
            org_id=org_id,
            user_id=user_id,
            agent_id=agent_id,
            agent_name=record.agent,
            status=record.status.value,
            provider=record.model_provider,
            model=record.model_name,
            prompt_version=record.prompt_version,
            site_slug=record.input.get("site"),
            input=record.input,
            output=record.output,
            error_kind=record.error_kind,
            error=record.error,
            attempts=record.attempts,
            input_tokens=record.usage.input_tokens,
            output_tokens=record.usage.output_tokens,
            tokens_estimated=record.usage.estimated,
            started_at=record.started_at,
            finished_at=record.finished_at,
            record=record.model_dump(mode="json"),
        )
    )
    await session.flush()


async def list_runs(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    agent_id: str | None = None,
    only_sites: Collection[str] | None = None,
    limit: int = 50,
) -> list[Run]:
    """Runs of one organisation, newest first. `only_sites` limits them to runs for those sites,
    for a person who may not see every site."""
    query = select(Run).where(Run.org_id == org_id).order_by(Run.started_at.desc()).limit(limit)
    if agent_id is not None:
        query = query.where(Run.agent_id == agent_id)
    if only_sites is not None:
        query = query.where(Run.site_slug.in_(only_sites))
    return list((await session.execute(query)).scalars())


async def get_run(
    session: AsyncSession,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    only_sites: Collection[str] | None = None,
) -> Run | None:
    query = select(Run).where(Run.org_id == org_id, Run.id == run_id)
    if only_sites is not None:
        query = query.where(Run.site_slug.in_(only_sites))
    return (await session.execute(query)).scalar_one_or_none()


# ------------------------------------------------------------------ audit and agent settings


async def audit(
    session: AsyncSession,
    *,
    org_id: uuid.UUID | None,
    actor_id: uuid.UUID | None = None,
    actor_email: str | None = None,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditEvent(
            org_id=org_id,
            actor_user_id=actor_id,
            actor_email=actor_email,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail or {},
        )
    )
    await session.flush()


async def get_agent_overrides(session: AsyncSession) -> dict[str, dict[str, Any]]:
    rows = await session.execute(select(AgentSetting))
    return {row.agent_id: dict(row.overrides) for row in rows.scalars()}


async def set_agent_overrides(
    session: AsyncSession,
    agent_id: str,
    overrides: dict[str, Any],
    updated_by: uuid.UUID | None,
) -> None:
    """Replace an agent's stored settings. Only a platform scoped session may write them."""
    statement = insert(AgentSetting).values(
        agent_id=agent_id, overrides=overrides, updated_by=updated_by, updated_at=func.now()
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[AgentSetting.agent_id],
            set_={
                "overrides": overrides,
                "updated_by": updated_by,
                "updated_at": func.now(),
            },
        )
    )
