"""The authenticated platform API against a real PostgreSQL, as the restricted application role.

These tests are about who can see and do what: sign-in, roles, site access and, above all, that
one organisation (or one limited person) can never reach another's data, through the API, the
database or the MCP tools an agent uses.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import jwt
import pytest
import pytest_asyncio
from mcp import Client
from pydantic import SecretStr
from sqlalchemy import text

from conftest import APP_URL
from energyops.agents.registry import AGENTS
from energyops.config import Settings
from energyops.db import repo
from energyops.db.seed import seed_demo
from energyops.db.session import Database, Scope
from energyops.models.test_adapter import TestAdapter
from energyops.security.auth import authenticate
from energyops.security.tokens import Claims, LocalTokens
from energyops.service.app import create_service_app
from energyops.service.data import SiteNotFound, load_site, sources_for

SECRET = SecretStr("s" * 48)
TOKENS = LocalTokens(SECRET)

ADMIN = "admin@harbour.example"
MANAGER = "manager@harbour.example"
TECH = "tech@harbour.example"  # limited to demo-hotel
VIEWER = "viewer@harbour.example"
NORTH = "admin@northside.example"
PLATFORM = "platform@energyops.example"


def settings() -> Settings:
    return Settings(database_url=SecretStr(APP_URL), local_jwt_secret=SECRET)


def make_app(db: Database, **kwargs: Any) -> Any:
    return create_service_app(settings(), db=db, **kwargs)


@pytest_asyncio.fixture
async def seeded(db: Database) -> Database:
    await seed_demo(db)
    return db


@pytest_asyncio.fixture
async def api(seeded: Database) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=make_app(seeded))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


def as_user(email: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS.issue(email)}"}


# ------------------------------------------------------------------ signing in


async def test_no_token_means_401(api: httpx.AsyncClient) -> None:
    for path in ("/me", "/sites", "/agents", "/runs"):
        response = await api.get(path)
        assert response.status_code == 401, path
        assert response.headers["www-authenticate"] == "Bearer"


async def test_a_garbage_or_expired_or_wrongly_signed_token_means_401(
    api: httpx.AsyncClient,
) -> None:
    now = int(time.time())
    claims = {
        "iss": "energyops-local",
        "aud": "energyops-local",
        "sub": ADMIN,
        "email": ADMIN,
        "exp": now + 600,
    }
    forged = jwt.encode(claims, "z" * 48, algorithm="HS256")
    for token in ("garbage", TOKENS.issue(ADMIN, minutes=-10), forged):
        response = await api.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401


async def test_a_valid_token_for_someone_never_added_means_403(api: httpx.AsyncClient) -> None:
    response = await api.get("/me", headers=as_user("ghost@nowhere.example"))
    assert response.status_code == 403


async def test_a_switched_off_person_or_organisation_is_refused_whatever_their_token(
    api: httpx.AsyncClient, owner_db: Database
) -> None:
    assert (await api.get("/me", headers=as_user(VIEWER))).status_code == 200
    async with owner_db.session(Scope.platform_wide()) as session:
        await session.execute(
            text("update users set is_active = false where email = :e"), {"e": VIEWER}
        )
    assert (await api.get("/me", headers=as_user(VIEWER))).status_code == 403
    assert (await api.get("/me", headers=as_user(NORTH))).status_code == 200
    async with owner_db.session(Scope.platform_wide()) as session:
        await session.execute(
            text("update organizations set is_active = false where slug = 'northside-health'")
        )
    assert (await api.get("/me", headers=as_user(NORTH))).status_code == 403


async def test_dev_login_gives_a_token_that_works_and_refuses_strangers(
    api: httpx.AsyncClient,
) -> None:
    response = await api.post("/auth/dev-login", json={"email": "Manager@Harbour.example"})
    assert response.status_code == 200
    token = response.json()["access_token"]
    me = await api.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["role"] == "manager"
    assert (await api.post("/auth/dev-login", json={"email": "ghost@x.example"})).status_code == 403


async def test_dev_login_does_not_exist_without_the_local_secret(seeded: Database) -> None:
    app = create_service_app(Settings(database_url=SecretStr(APP_URL)), db=seeded, verifier=TOKENS)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/auth/dev-login", json={"email": ADMIN})
    assert response.status_code == 404


async def test_a_token_cannot_choose_its_own_organisation_or_role(api: httpx.AsyncClient) -> None:
    now = int(time.time())
    claims = {
        "iss": "energyops-local",
        "aud": "energyops-local",
        "sub": VIEWER,
        "email": VIEWER,
        "exp": now + 600,
        "role": "platform_admin",
        "org_id": "00000000-0000-0000-0000-000000000000",
        "roles": ["org_admin"],
    }
    token = jwt.encode(claims, SECRET.get_secret_value(), algorithm="HS256")
    me = await api.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["role"] == "viewer"
    assert me.json()["organisation"] == "Harbour Hotels"


async def test_me_describes_the_person_from_the_database(api: httpx.AsyncClient) -> None:
    body = (await api.get("/me", headers=as_user(TECH))).json()
    assert body["role"] == "technician"
    assert body["organisation"] == "Harbour Hotels"
    assert [s["slug"] for s in body["sites"]] == ["demo-hotel"]
    assert "run_agents" in body["permissions"] and "approve_tickets" not in body["permissions"]


# ------------------------------------------------------------------ tenant isolation


async def test_each_organisation_sees_only_its_own_sites(api: httpx.AsyncClient) -> None:
    harbour = (await api.get("/sites", headers=as_user(ADMIN))).json()
    northside = (await api.get("/sites", headers=as_user(NORTH))).json()
    assert [s["slug"] for s in harbour] == ["demo-office", "demo-hotel"] or sorted(
        s["slug"] for s in harbour
    ) == ["demo-hotel", "demo-office"]
    assert [s["slug"] for s in northside] == ["demo-clinic"]


@pytest.mark.parametrize("suffix", ["summary", "series", "anomalies", "tariff"])
async def test_another_organisations_site_looks_like_one_that_does_not_exist(
    api: httpx.AsyncClient, suffix: str
) -> None:
    others = await api.get(f"/sites/demo-hotel/{suffix}", headers=as_user(NORTH))
    missing = await api.get(f"/sites/no-such-site/{suffix}", headers=as_user(NORTH))
    assert others.status_code == missing.status_code == 404
    assert others.json() == missing.json()
    assert (await api.get(f"/sites/demo-hotel/{suffix}", headers=as_user(ADMIN))).status_code == 200


async def test_another_organisation_cannot_run_agents_on_or_change_a_site(
    api: httpx.AsyncClient,
) -> None:
    run = await api.post(
        "/agents/savings-advisor/runs", json={"site": "demo-hotel"}, headers=as_user(NORTH)
    )
    assert run.status_code == 404
    tariff = {"label": "Mine now", "currency": "GBP", "price_per_kwh": 0.01}
    put = await api.put("/sites/demo-hotel/tariff", json=tariff, headers=as_user(NORTH))
    assert put.status_code == 404
    still = (await api.get("/sites/demo-hotel/tariff", headers=as_user(ADMIN))).json()
    assert still["label"] != "Mine now"


async def test_runs_are_private_to_the_organisation_that_made_them(api: httpx.AsyncClient) -> None:
    made = await api.post(
        "/agents/savings-advisor/runs", json={"site": "demo-hotel"}, headers=as_user(MANAGER)
    )
    assert made.status_code == 200
    run_id = made.json()["run_id"]
    assert (await api.get(f"/runs/{run_id}", headers=as_user(MANAGER))).status_code == 200
    assert (await api.get(f"/runs/{run_id}", headers=as_user(NORTH))).status_code == 404
    assert (await api.get("/runs", headers=as_user(NORTH))).json() == []


async def test_a_database_query_with_no_filter_still_sees_one_organisation(
    seeded: Database,
) -> None:
    """Even code that forgets to filter cannot leak, because the database refuses."""
    north = await authenticate(
        seeded, Claims(subject=NORTH, email=NORTH, name=None, external_id=None)
    )
    async with seeded.session(north.scope) as session:
        sites = (await session.execute(text("select slug from sites"))).scalars().all()
        readings = (
            await session.execute(text("select count(distinct meter_id) from readings"))
        ).scalar_one()
    assert sites == ["demo-clinic"]
    assert readings == 1


# ------------------------------------------------------------------ a person limited to some sites


async def test_a_limited_person_sees_only_their_sites_and_gets_the_same_answer_for_others(
    api: httpx.AsyncClient,
) -> None:
    sites = (await api.get("/sites", headers=as_user(TECH))).json()
    assert [s["slug"] for s in sites] == ["demo-hotel"]
    forbidden = await api.get("/sites/demo-office/summary", headers=as_user(TECH))
    missing = await api.get("/sites/no-such-site/summary", headers=as_user(TECH))
    assert forbidden.status_code == missing.status_code == 404
    assert forbidden.json() == missing.json()
    run = await api.post(
        "/agents/energy-analyst/runs",
        json={"site": "demo-office", "question": "Summarise."},
        headers=as_user(TECH),
    )
    assert run.status_code == 404


async def test_a_limited_person_does_not_see_runs_on_sites_they_cannot_see(
    api: httpx.AsyncClient,
) -> None:
    office = await api.post(
        "/agents/savings-advisor/runs", json={"site": "demo-office"}, headers=as_user(MANAGER)
    )
    hotel = await api.post(
        "/agents/savings-advisor/runs", json={"site": "demo-hotel"}, headers=as_user(MANAGER)
    )
    assert office.status_code == hotel.status_code == 200
    tech_runs = (await api.get("/runs", headers=as_user(TECH))).json()
    assert [r["run_id"] for r in tech_runs] == [hotel.json()["run_id"]]
    seen = await api.get(f"/runs/{office.json()['run_id']}", headers=as_user(TECH))
    assert seen.status_code == 404
    manager_runs = (await api.get("/runs", headers=as_user(MANAGER))).json()
    assert len(manager_runs) == 2


async def test_a_run_with_no_site_named_uses_the_first_site_the_person_may_see(
    api: httpx.AsyncClient,
) -> None:
    record = (await api.post("/agents/savings-advisor/runs", json={}, headers=as_user(TECH))).json()
    assert record["input"]["site"] == "demo-hotel"


async def test_the_data_an_agent_is_given_holds_one_site_and_the_mcp_tools_cannot_widen_it(
    seeded: Database,
) -> None:
    """Backlog item 9: an agent acting for a limited person cannot read a site they may not see,
    even by asking its MCP tools for it by name."""
    tech = await authenticate(seeded, Claims(subject=TECH, email=TECH, name=None, external_id=None))
    with pytest.raises(SiteNotFound):
        await load_site(seeded, tech, "demo-office")
    data = sources_for(await load_site(seeded, tech, "demo-hotel"))
    assert data.mcp_target is not None
    async with Client(data.mcp_target) as client:
        listed = await client.call_tool("list_sites", {})
        assert listed.structured_content is not None
        assert listed.structured_content["sites"] == ["demo-hotel"]
        for tool in ("get_fact_sheet", "get_anomalies", "get_savings_facts"):
            result = await client.call_tool(tool, {"site": "demo-office"})
            assert result.is_error, tool
            assert "unknown_site" in result.content[0].text  # type: ignore[union-attr]
        allowed = await client.call_tool("get_fact_sheet", {"site": "demo-hotel"})
        assert not allowed.is_error
    with pytest.raises(LookupError):
        data.readings("demo-office")
    with pytest.raises(LookupError):
        data.tariffs("demo-clinic")


# ------------------------------------------------------------------ roles


async def test_a_platform_admin_cannot_read_customer_data_or_run_agents(
    api: httpx.AsyncClient,
) -> None:
    me = (await api.get("/me", headers=as_user(PLATFORM))).json()
    assert me["role"] == "platform_admin" and me["organisation"] is None and me["sites"] == []
    for path in ("/sites", "/runs", "/agents", "/sites/demo-hotel/summary"):
        assert (await api.get(path, headers=as_user(PLATFORM))).status_code == 403, path
    run = await api.post("/agents/savings-advisor/runs", json={}, headers=as_user(PLATFORM))
    assert run.status_code == 403


async def test_a_viewer_can_look_but_not_run_or_change(api: httpx.AsyncClient) -> None:
    assert (await api.get("/sites/demo-hotel/summary", headers=as_user(VIEWER))).status_code == 200
    assert (await api.get("/runs", headers=as_user(VIEWER))).status_code == 200
    run = await api.post("/agents/savings-advisor/runs", json={}, headers=as_user(VIEWER))
    assert run.status_code == 403
    tariff = {"label": "x", "currency": "GBP", "price_per_kwh": 0.2}
    put = await api.put("/sites/demo-hotel/tariff", json=tariff, headers=as_user(VIEWER))
    assert put.status_code == 403


async def test_a_technician_runs_agents_but_cannot_change_a_tariff(api: httpx.AsyncClient) -> None:
    run = await api.post("/agents/savings-advisor/runs", json={}, headers=as_user(TECH))
    assert run.status_code == 200
    tariff = {"label": "x", "currency": "GBP", "price_per_kwh": 0.2}
    assert (
        await api.put("/sites/demo-hotel/tariff", json=tariff, headers=as_user(TECH))
    ).status_code == 403


async def test_a_manager_can_set_a_tariff_and_the_savings_advisor_uses_it(
    api: httpx.AsyncClient,
) -> None:
    body = {
        "label": "Hotel contract 2027",
        "currency": "EUR",
        "price_per_kwh": 0.2,
        "bands": [{"name": "Day", "start_hour": 8, "end_hour": 20, "price_per_kwh": 0.3}],
        "demand_charge_per_kw_month": 4.0,
    }
    put = await api.put("/sites/demo-hotel/tariff", json=body, headers=as_user(MANAGER))
    assert put.status_code == 200 and put.json()["symbol"] == "€" and put.json()["is_demo"] is False
    got = (await api.get("/sites/demo-hotel/tariff", headers=as_user(VIEWER))).json()
    assert got["label"] == "Hotel contract 2027"
    run = (
        await api.post(
            "/agents/savings-advisor/runs", json={"site": "demo-hotel"}, headers=as_user(MANAGER)
        )
    ).json()
    assert run["status"] == "succeeded"


async def test_an_unusable_tariff_is_a_422(api: httpx.AsyncClient) -> None:
    overlapping = {
        "label": "x",
        "currency": "GBP",
        "price_per_kwh": 0.2,
        "bands": [
            {"name": "A", "start_hour": 6, "end_hour": 12, "price_per_kwh": 0.3},
            {"name": "B", "start_hour": 10, "end_hour": 14, "price_per_kwh": 0.1},
        ],
    }
    response = await api.put("/sites/demo-hotel/tariff", json=overlapping, headers=as_user(MANAGER))
    assert response.status_code == 422 and "overlap" in response.json()["detail"]
    negative = {"label": "x", "currency": "GBP", "price_per_kwh": -1}
    assert (
        await api.put("/sites/demo-hotel/tariff", json=negative, headers=as_user(MANAGER))
    ).status_code == 422


# ------------------------------------------------------------------ running agents


async def test_every_agent_runs_on_the_callers_data_with_the_labelled_test_adapter(
    api: httpx.AsyncClient,
) -> None:
    inputs = {
        "energy-analyst": {"question": "Summarise this site's energy use over the period."},
        "report-writer": {"audience": "manager"},
        "savings-advisor": {},
    }
    for agent_id, body in inputs.items():
        response = await api.post(
            f"/agents/{agent_id}/runs",
            json={"site": "demo-hotel", **body},
            headers=as_user(MANAGER),
        )
        assert response.status_code == 200, (agent_id, response.text)
        record = response.json()
        assert record["status"] == "succeeded", (agent_id, record["error"])
        assert record["model_provider"] == "test-adapter"
        assert record["input"]["site"] == "demo-hotel"


async def test_the_anomaly_investigator_runs_on_an_event_found_in_the_callers_data(
    api: httpx.AsyncClient,
) -> None:
    events = (await api.get("/sites/demo-clinic/anomalies", headers=as_user(NORTH))).json()
    assert events["anomalies"], "the clinic has built-in events"
    response = await api.post(
        "/agents/anomaly-investigator/runs",
        json={"site": "demo-clinic", "anomaly_id": events["anomalies"][0]["id"]},
        headers=as_user(NORTH),
    )
    assert response.json()["status"] == "succeeded"


async def test_a_caller_cannot_pick_the_provider_the_model_or_how_data_is_read(
    api: httpx.AsyncClient,
) -> None:
    for extra in ({"provider": "gemini"}, {"model": "big"}, {"data_access": "direct"}):
        response = await api.post(
            "/agents/savings-advisor/runs",
            json={"site": "demo-hotel", **extra},
            headers=as_user(MANAGER),
        )
        assert response.status_code == 422, extra


async def test_wrong_inputs_are_a_422_and_an_unknown_agent_a_404(api: httpx.AsyncClient) -> None:
    bad = await api.post(
        "/agents/energy-analyst/runs", json={"site": "demo-hotel"}, headers=as_user(MANAGER)
    )
    assert bad.status_code == 422
    missing = await api.post("/agents/nope/runs", json={}, headers=as_user(MANAGER))
    assert missing.status_code == 404


async def test_a_run_that_fails_its_checks_is_still_stored(seeded: Database) -> None:
    app = make_app(seeded, client_factory=lambda provider, model, keys, reply: TestAdapter("{}"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/agents/savings-advisor/runs", json={"site": "demo-hotel"}, headers=as_user(MANAGER)
        )
        assert response.status_code == 200 and response.json()["status"] == "failed"
        listed = (await client.get("/runs", headers=as_user(MANAGER))).json()
    assert [r["status"] for r in listed] == ["failed"]


async def test_a_switched_off_agent_cannot_be_run_or_listed(
    api: httpx.AsyncClient, seeded: Database
) -> None:
    assert len((await api.get("/agents", headers=as_user(MANAGER))).json()) == len(AGENTS)
    async with seeded.session(Scope.platform_wide()) as session:
        await repo.set_agent_overrides(
            session, "savings-advisor", {"status": "draft", "provider": "test"}, None
        )
    listed = (await api.get("/agents", headers=as_user(MANAGER))).json()
    assert "savings-advisor" not in [a["id"] for a in listed]
    run = await api.post("/agents/savings-advisor/runs", json={}, headers=as_user(MANAGER))
    assert run.status_code == 409


async def test_an_agent_with_no_model_set_up_is_a_503_not_a_guess(
    api: httpx.AsyncClient, seeded: Database
) -> None:
    async with seeded.session(Scope.platform_wide()) as session:
        await repo.set_agent_overrides(session, "savings-advisor", {"status": "active"}, None)
    run = await api.post("/agents/savings-advisor/runs", json={}, headers=as_user(MANAGER))
    assert run.status_code == 503  # the built-in model is Gemini and no key is set


# ------------------------------------------------------------------ records


async def test_runs_and_tariff_changes_are_written_to_the_audit_log(
    api: httpx.AsyncClient, seeded: Database
) -> None:
    await api.post("/agents/savings-advisor/runs", json={}, headers=as_user(MANAGER))
    tariff = {"label": "x", "currency": "GBP", "price_per_kwh": 0.2}
    await api.put("/sites/demo-hotel/tariff", json=tariff, headers=as_user(MANAGER))
    manager = await authenticate(
        seeded, Claims(subject=MANAGER, email=MANAGER, name=None, external_id=None)
    )
    async with seeded.session(manager.scope) as session:
        rows = (
            await session.execute(
                text("select action, actor_email from audit_events order by created_at")
            )
        ).all()
    assert [r[0] for r in rows] == ["agent.run", "tariff.set"]
    assert {r[1] for r in rows} == {MANAGER}
    north = await authenticate(
        seeded, Claims(subject=NORTH, email=NORTH, name=None, external_id=None)
    )
    async with seeded.session(north.scope) as session:
        assert (await session.execute(text("select count(*) from audit_events"))).scalar_one() == 0


async def test_responses_are_never_cached(api: httpx.AsyncClient) -> None:
    response = await api.get("/me", headers=as_user(ADMIN))
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_series_and_summary_give_the_callers_numbers(api: httpx.AsyncClient) -> None:
    series = (await api.get("/sites/demo-hotel/series?days=2", headers=as_user(VIEWER))).json()
    assert 24 <= len(series) <= 72 and all(point["kw"] >= 0 for point in series)
    summary = (await api.get("/sites/demo-hotel/summary", headers=as_user(VIEWER))).json()
    assert summary["meter_id"] == "demo-hotel" and summary["facts"]
    assert (
        await api.get("/sites/demo-hotel/series?days=99", headers=as_user(VIEWER))
    ).status_code == 422
