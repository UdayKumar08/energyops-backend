"""Tests against a real PostgreSQL, connected as the restricted application role."""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

from energyops.agents.runs import RunRecord
from energyops.agents.sources import DEMO_TARIFFS, synthetic_demo_source
from energyops.db import repo
from energyops.db.base import Base
from energyops.db.checkpoint import CHECKPOINT_TABLES
from energyops.db.models import TENANT_TABLES, Run
from energyops.db.seed import DEMO_ORGS, PLATFORM_EMAIL, seed_demo
from energyops.db.session import Database, Scope
from energyops.synthetic import MeterReading
from energyops.tariffs import Tariff, TariffBand, TariffError

START = datetime(2026, 3, 2, tzinfo=UTC)


async def scalar(db: Database, scope: Scope, sql: str, **params):
    async with db.session(scope) as session:
        return (await session.execute(text(sql), params)).scalar_one()


async def column(db: Database, scope: Scope, sql: str, **params) -> list:
    async with db.session(scope) as session:
        return list((await session.execute(text(sql), params)).scalars())


# ------------------------------------------------------------------ the schema


def test_the_migrations_match_the_models(migrated_db: str) -> None:
    engine = create_engine(migrated_db)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            differences = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()
    # The descending-order indexes are written as raw SQL expressions, which the comparison
    # cannot read back, so they are the only differences allowed.
    unexpected = [
        d
        for d in differences
        if "created_at desc" not in str(d)
        and "started_at desc" not in str(d)
        and not any(table in str(d) for table in CHECKPOINT_TABLES)  # LangGraph's, not ours
    ]
    assert unexpected == []


async def test_the_application_role_owns_nothing_and_cannot_change_the_schema(db: Database) -> None:
    async with db.session(Scope.platform_wide()) as session:
        owned = (
            await session.execute(
                text("select count(*) from pg_tables where tableowner = current_user")
            )
        ).scalar_one()
        assert owned == 0
        is_super = (
            await session.execute(
                text("select rolsuper or rolbypassrls from pg_roles where rolname = current_user")
            )
        ).scalar_one()
        assert is_super is False
    with pytest.raises(ProgrammingError):
        async with db.session(Scope.platform_wide()) as session:
            await session.execute(text("create table sneaky (x int)"))
    with pytest.raises(ProgrammingError):
        async with db.session(Scope.platform_wide()) as session:
            await session.execute(text("alter table readings disable row level security"))


async def test_every_table_with_an_organisation_has_row_level_security(db: Database) -> None:
    async with db.session(Scope.platform_wide()) as session:
        rows = (
            await session.execute(
                text(
                    "select c.relname, c.relrowsecurity from pg_class c "
                    "join pg_namespace n on n.oid = c.relnamespace "
                    "where n.nspname = 'public' and c.relkind = 'r' "
                    "and c.relname <> 'alembic_version'"
                )
            )
        ).all()
    unprotected = sorted(name for name, secured in rows if not secured)
    # LangGraph's saved state has no organisation column, so row level security cannot apply to
    # it. It is reached only for a ticket id read through the protected tickets table first.
    assert set(unprotected) == set(CHECKPOINT_TABLES)
    names = {name for name, _ in rows}
    assert set(TENANT_TABLES) <= names


# ------------------------------------------------------------------ isolation


async def two_organisations(db: Database):
    result = await seed_demo(db)  # through the restricted role: the real flows must be allowed
    return result.organizations["harbour-hotels"], result.organizations["northside-health"]


async def test_the_seed_runs_through_the_restricted_role_and_is_repeatable(db: Database) -> None:
    first = await seed_demo(db)
    again = await seed_demo(db)
    assert first.created and not again.created and first.organizations == again.organizations
    assert len(first.organizations) == len(DEMO_ORGS)
    assert await scalar(db, Scope.platform_wide(), "select count(*) from users") == 7
    harbour = first.organizations["harbour-hotels"]
    assert await scalar(db, Scope.for_org(harbour), "select count(*) from readings") == 2 * 28 * 96


async def test_a_query_with_no_filter_sees_only_the_signed_in_organisation(db: Database) -> None:
    harbour, northside = await two_organisations(db)
    for org, expected in ((harbour, ["demo-hotel", "demo-office"]), (northside, ["demo-clinic"])):
        scope = Scope.for_org(org)
        assert sorted(await column(db, scope, "select slug from sites")) == expected
        orgs_seen = set(await column(db, scope, "select distinct org_id from readings"))
        assert orgs_seen == {org}
        assert await scalar(db, scope, "select count(*) from tariffs") == len(expected)
        assert await scalar(db, scope, "select count(*) from meters") == len(expected)


async def test_a_session_for_no_organisation_sees_no_customer_data_at_all(db: Database) -> None:
    await two_organisations(db)
    nobody = Scope()
    for table in TENANT_TABLES:
        assert await scalar(db, nobody, f"select count(*) from {table}") == 0
    assert await scalar(db, nobody, "select count(*) from users") == 0
    assert await scalar(db, nobody, "select count(*) from organizations") == 0


async def test_platform_staff_see_organisations_and_people_but_never_customer_data(
    db: Database,
) -> None:
    await two_organisations(db)
    platform = Scope.platform_wide()
    assert await scalar(db, platform, "select count(*) from organizations") == 2
    assert await scalar(db, platform, "select count(*) from users") == 7
    for table in TENANT_TABLES:
        assert await scalar(db, platform, f"select count(*) from {table}") == 0, table


async def test_people_see_only_their_own_organisation_and_never_the_platform_account(
    db: Database,
) -> None:
    harbour, _ = await two_organisations(db)
    emails = await column(db, Scope.for_org(harbour), "select email from users order by 1")
    assert emails == [
        "admin@harbour.example",
        "manager@harbour.example",
        "tech@harbour.example",
        "viewer@harbour.example",
    ]
    assert PLATFORM_EMAIL not in emails
    assert await column(db, Scope.for_org(harbour), "select name from organizations") == [
        "Harbour Hotels"
    ]


async def test_one_organisation_cannot_write_into_or_change_another(db: Database) -> None:
    harbour, northside = await two_organisations(db)
    # Insert a site that claims to belong to the other organisation.
    with pytest.raises(DBAPIError, match="row-level security"):
        async with db.session(Scope.for_org(harbour)) as session:
            await repo.create_site(session, northside, "stolen", "Stolen")
    # Update and delete reach no rows, because the rows are not visible.
    async with db.session(Scope.for_org(harbour)) as session:
        changed = await session.execute(
            text("update sites set name = 'hacked' where org_id = :o"), {"o": northside}
        )
        removed = await session.execute(
            text("delete from readings where org_id = :o"), {"o": northside}
        )
        assert changed.rowcount == 0 and removed.rowcount == 0
    assert (
        await scalar(db, Scope.for_org(northside), "select name from sites") == "Northside Clinic"
    )
    assert await scalar(db, Scope.for_org(northside), "select count(*) from readings") == 28 * 96
    # Moving one's own row to another organisation is refused too.
    with pytest.raises(DBAPIError, match="row-level security"):
        async with db.session(Scope.for_org(harbour)) as session:
            await session.execute(text("update sites set org_id = :o"), {"o": northside})


async def test_an_organisation_cannot_create_a_platform_account_or_organisation(
    db: Database,
) -> None:
    harbour, _ = await two_organisations(db)
    with pytest.raises(DBAPIError):
        async with db.session(Scope.for_org(harbour)) as session:
            await repo.create_organization(session, "Sneaky", "sneaky")
    with pytest.raises((IntegrityError, DBAPIError)):
        async with db.session(Scope.for_org(harbour)) as session:
            await repo.create_user(
                session,
                org_id=harbour,
                email="p@x.example",
                display_name="P",
                role="platform_admin",
            )


async def test_a_platform_account_must_have_no_organisation_and_others_must_have_one(
    db: Database,
) -> None:
    harbour, _ = await two_organisations(db)
    with pytest.raises(IntegrityError):
        async with db.session(Scope.platform_wide()) as session:
            await repo.create_user(
                session, org_id=None, email="a@x.example", display_name="A", role="manager"
            )
    with pytest.raises(IntegrityError):
        async with db.session(Scope.platform_wide()) as session:
            await repo.create_user(
                session,
                org_id=harbour,
                email="b@x.example",
                display_name="B",
                role="platform_admin",
            )
    with pytest.raises(IntegrityError):
        async with db.session(Scope.platform_wide()) as session:
            await repo.create_user(
                session, org_id=harbour, email="c@x.example", display_name="C", role="superhero"
            )


async def test_email_addresses_are_unique_regardless_of_case(db: Database) -> None:
    harbour, _ = await two_organisations(db)
    with pytest.raises(IntegrityError):
        async with db.session(Scope.platform_wide()) as session:
            await repo.create_user(
                session,
                org_id=harbour,
                email="ADMIN@harbour.example",
                display_name="Dup",
                role="viewer",
            )


async def test_agent_settings_can_be_read_by_anyone_but_changed_only_by_the_platform(
    db: Database,
) -> None:
    harbour, _ = await two_organisations(db)
    write = (
        "insert into agent_settings (agent_id, overrides) "
        "values ('scratch-agent', '{\"temperature\": 0.2}')"
    )
    with pytest.raises(DBAPIError, match="row-level security"):
        async with db.session(Scope.for_org(harbour)) as session:
            await session.execute(text(write))
    async with db.session(Scope.platform_wide()) as session:
        await session.execute(text(write))
    async with db.session(Scope.for_org(harbour)) as session:
        stored = await repo.get_agent_overrides(session)
    assert stored["scratch-agent"] == {"temperature": 0.2}


# ------------------------------------------------------------------ sites and access


async def test_a_person_limited_to_some_sites_sees_only_those(db: Database) -> None:
    harbour, _ = await two_organisations(db)
    # Signing in is two steps: find the person (platform scope, the organisation is not known
    # yet), then read their site access inside their own organisation.
    async with db.session(Scope.platform_wide()) as session:
        tech = await repo.find_user(session, email="TECH@harbour.example")
        manager = await repo.find_user(session, email="manager@harbour.example")
    assert tech is not None and manager is not None
    async with db.session(Scope.for_org(harbour)) as session:
        limited = await repo.allowed_site_ids(session, tech)
        everything = await repo.allowed_site_ids(session, manager)
    assert everything is None and limited is not None and len(limited) == 1
    async with db.session(Scope.for_org(harbour)) as session:
        assert [s.slug for s in await repo.list_sites(session, harbour, limited)] == ["demo-hotel"]
        # Listed by name: "Harbour Head Office" sorts before "Harbour Hotel".
        assert [s.slug for s in await repo.list_sites(session, harbour, everything)] == [
            "demo-office",
            "demo-hotel",
        ]
        assert await repo.get_site(session, harbour, "demo-hotel", limited) is not None
        # A site the person may not see looks exactly like a site that does not exist.
        hidden = await repo.get_site(session, harbour, "demo-office", limited)
        missing = await repo.get_site(session, harbour, "no-such-site", limited)
        assert hidden is None and missing is None


async def test_a_site_slug_only_resolves_inside_its_own_organisation(db: Database) -> None:
    harbour, northside = await two_organisations(db)
    async with db.session(Scope.for_org(harbour)) as session:
        assert await repo.get_site(session, harbour, "demo-clinic", None) is None
    async with db.session(Scope.for_org(northside)) as session:
        assert await repo.get_site(session, northside, "demo-clinic", None) is not None
        # Even asking with the other organisation's id finds nothing: the session's scope wins.
        assert await repo.get_site(session, harbour, "demo-hotel", None) is None


async def test_the_same_site_slug_can_exist_in_two_organisations(db: Database) -> None:
    harbour, northside = await two_organisations(db)
    for org in (harbour, northside):
        async with db.session(Scope.for_org(org)) as session:
            await repo.create_site(session, org, "shared-name", "Same slug")
    assert (
        await scalar(
            db, Scope.for_org(harbour), "select count(*) from sites where slug = 'shared-name'"
        )
        == 1
    )


# ------------------------------------------------------------------ readings


def reading(offset_minutes: int, kw: float) -> MeterReading:
    return MeterReading("x", START + timedelta(minutes=offset_minutes), 15, kw)


async def test_readings_round_trip_and_are_replaced_not_duplicated(db: Database) -> None:
    harbour, _ = await two_organisations(db)
    async with db.session(Scope.for_org(harbour)) as session:
        site, meter = await repo.create_site(session, harbour, "round-trip", "Round trip")
        await repo.upsert_readings(session, harbour, meter.id, [reading(0, 1.5), reading(15, 2.5)])
        await repo.upsert_readings(session, harbour, meter.id, [reading(15, 9.0), reading(30, 3.5)])
    async with db.session(Scope.for_org(harbour)) as session:
        meter = await repo.primary_meter(session, harbour, site.id)
        assert meter is not None
        loaded = await repo.load_readings(session, harbour, meter, label="round-trip", days=1)
        span = await repo.reading_range(session, harbour, meter.id)
    assert [(r.timestamp, r.power_kw) for r in loaded] == [
        (START, 1.5),
        (START + timedelta(minutes=15), 9.0),
        (START + timedelta(minutes=30), 3.5),
    ]
    assert all(r.meter_id == "round-trip" and r.interval_minutes == 15 for r in loaded)
    assert span == (START, START + timedelta(minutes=30))


async def test_the_window_is_the_last_n_utc_days_ending_at_the_latest_reading(db: Database) -> None:
    harbour, _ = await two_organisations(db)
    async with db.session(Scope.for_org(harbour)) as session:
        site = await repo.get_site(session, harbour, "demo-hotel", None)
        assert site is not None
        meter = await repo.primary_meter(session, harbour, site.id)
        assert meter is not None
        week = await repo.load_readings(session, harbour, meter, label="demo-hotel", days=7)
        all_of_it = await repo.load_readings(session, harbour, meter, label="demo-hotel", days=100)
    assert len(week) == 7 * 96 and week[0].timestamp == START + timedelta(days=21)
    assert len(all_of_it) == 28 * 96
    assert [r.power_kw for r in all_of_it[: 7 * 96]] == [
        r.power_kw for r in synthetic_demo_source(days=28)("demo-hotel")[: 7 * 96]
    ]


async def test_a_meter_with_no_readings_loads_nothing(db: Database) -> None:
    harbour, _ = await two_organisations(db)
    async with db.session(Scope.for_org(harbour)) as session:
        _, meter = await repo.create_site(session, harbour, "empty", "Empty")
        assert await repo.load_readings(session, harbour, meter, label="empty", days=7) == []
        assert await repo.reading_range(session, harbour, meter.id) is None


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, 2e6])
async def test_the_database_itself_refuses_impossible_power_values(
    db: Database, bad: float
) -> None:
    harbour, _ = await two_organisations(db)
    with pytest.raises(IntegrityError):
        async with db.session(Scope.for_org(harbour)) as session:
            _, meter = await repo.create_site(session, harbour, "bad", "Bad")
            await repo.upsert_readings(session, harbour, meter.id, [reading(0, bad)])


async def test_a_second_primary_meter_for_a_site_is_refused(db: Database) -> None:
    harbour, _ = await two_organisations(db)
    from energyops.db.models import Meter

    with pytest.raises(IntegrityError):
        async with db.session(Scope.for_org(harbour)) as session:
            site, _ = await repo.create_site(session, harbour, "twice", "Twice")
            session.add(Meter(org_id=harbour, site_id=site.id, code="second", is_primary=True))
            await session.flush()


# ------------------------------------------------------------------ tariffs


async def test_a_tariff_round_trips_with_its_bands_and_can_be_replaced_and_removed(
    db: Database,
) -> None:
    harbour, northside = await two_organisations(db)
    async with db.session(Scope.for_org(harbour)) as session:
        site = await repo.get_site(session, harbour, "demo-hotel", None)
        assert site is not None
        stored = await repo.get_tariff(session, harbour, site.id)
        assert stored == DEMO_TARIFFS["demo-hotel"]
        new = Tariff(
            "New",
            "EUR",
            0.3,
            bands=(TariffBand("Night", 0, 6, 0.1, days="all"),),
            demand_charge_per_kw_month=2.0,
        )
        await repo.set_tariff(session, harbour, site.id, new)
    async with db.session(Scope.for_org(harbour)) as session:
        assert await repo.get_tariff(session, harbour, site.id) == new
        await repo.remove_tariff(session, harbour, site.id)
    async with db.session(Scope.for_org(harbour)) as session:
        assert await repo.get_tariff(session, harbour, site.id) is None
    async with db.session(Scope.for_org(northside)) as session:
        clinic = await repo.get_site(session, northside, "demo-clinic", None)
        assert (
            clinic is not None and await repo.get_tariff(session, northside, clinic.id) is not None
        )


async def test_a_stored_tariff_that_is_not_usable_is_reported_not_guessed(db: Database) -> None:
    harbour, _ = await two_organisations(db)
    async with db.session(Scope.for_org(harbour)) as session:
        site = await repo.get_site(session, harbour, "demo-hotel", None)
        assert site is not None
        await session.execute(
            text('update tariffs set bands = \'[{"name": "x"}]\' where site_id = :s'),
            {"s": site.id},
        )
    with pytest.raises(TariffError):
        async with db.session(Scope.for_org(harbour)) as session:
            await repo.get_tariff(session, harbour, site.id)


# ------------------------------------------------------------------ runs and audit


async def a_run_record() -> RunRecord:
    from energyops.agents.registry import AGENTS, DataSources, RunInputs
    from energyops.agents.sources import demo_tariff_source
    from energyops.models.test_adapter import TestAdapter

    entry = AGENTS["energy-analyst"]
    data = DataSources(synthetic_demo_source(), demo_tariff_source())
    request = entry.make_request(RunInputs(question="Summarise.", site="demo-hotel"))
    agent = entry.build(
        entry.spec,
        TestAdapter(entry.test_reply(data, request)),
        entry.make_facts(entry.spec, "direct", data),
    )
    return await agent.run(request)


async def test_a_run_is_stored_whole_and_only_its_own_organisation_can_read_it(
    db: Database,
) -> None:
    harbour, northside = await two_organisations(db)
    record = await a_run_record()
    async with db.session(Scope.for_org(harbour)) as session:
        await repo.save_run(session, harbour, None, "energy-analyst", record)
    async with db.session(Scope.for_org(harbour)) as session:
        [row] = await repo.list_runs(session, harbour)
        assert row.agent_id == "energy-analyst" and row.status == "succeeded"
        assert row.site_slug == "demo-hotel" and row.provider == "test-adapter"
        assert RunRecord.model_validate(row.record) == record
        assert await repo.get_run(session, harbour, uuid.UUID(record.run_id)) is not None
        assert len(await repo.list_runs(session, harbour, agent_id="report-writer")) == 0
    async with db.session(Scope.for_org(northside)) as session:
        assert await repo.list_runs(session, northside) == []
        assert await repo.get_run(session, northside, uuid.UUID(record.run_id)) is None
        assert await scalar_in(session, "select count(*) from runs") == 0


async def scalar_in(session, sql: str):
    return (await session.execute(text(sql))).scalar_one()


async def test_the_audit_log_is_split_by_organisation_and_platform_rows_are_platform_only(
    db: Database,
) -> None:
    harbour, northside = await two_organisations(db)
    async with db.session(Scope.for_org(harbour)) as session:
        await repo.audit(session, org_id=harbour, action="site.created", detail={"slug": "x"})
    async with db.session(Scope.for_org(northside)) as session:
        await repo.audit(session, org_id=northside, action="site.created")
    async with db.session(Scope.platform_wide()) as session:
        await repo.audit(session, org_id=None, action="agent.updated")
    assert await scalar(db, Scope.for_org(harbour), "select count(*) from audit_events") == 1
    assert await scalar(db, Scope.platform_wide(), "select count(*) from audit_events") == 3
    assert (
        await scalar(
            db, Scope.for_org(harbour), "select count(*) from audit_events where org_id is null"
        )
        == 0
    )


async def test_the_scope_lasts_only_for_its_own_transaction(db: Database) -> None:
    harbour, _ = await two_organisations(db)
    await scalar(db, Scope.for_org(harbour), "select count(*) from sites")
    # A later session that is given no scope must not inherit the previous one from the pool.
    assert await scalar(db, Scope(), "select count(*) from sites") == 0
    assert (
        await scalar(db, Scope(), "select coalesce(current_setting('app.org_id', true), '')") == ""
    )


def test_run_is_a_tenant_table() -> None:
    assert Run.__tablename__ in TENANT_TABLES
