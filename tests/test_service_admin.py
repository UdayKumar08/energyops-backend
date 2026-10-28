"""The platform admin console's API, and what an organisation's own administrators manage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from energyops.agents.registry import AGENTS
from energyops.db import repo
from energyops.db.session import Database, Scope
from energyops.security.auth import authenticate
from energyops.security.tokens import Claims
from service_helpers import ADMIN, MANAGER, NORTH, PLATFORM, TECH, VIEWER, as_user

# ------------------------------------------------------------------ who may use the console


@pytest.mark.parametrize("email", [ADMIN, MANAGER, TECH, VIEWER, NORTH])
async def test_nobody_but_the_platform_admin_reaches_the_admin_routes(
    api: httpx.AsyncClient, email: str
) -> None:
    for path in ("/admin/agents", "/admin/organizations", "/admin/usage", "/admin/audit"):
        assert (await api.get(path, headers=as_user(email))).status_code == 403, path
    assert (await api.get("/admin/agents")).status_code == 401


# ------------------------------------------------------------------ agent registry and settings


async def test_the_registry_lists_every_agent_with_its_settings(api: httpx.AsyncClient) -> None:
    rows = (await api.get("/admin/agents", headers=as_user(PLATFORM))).json()
    assert [r["summary"]["id"] for r in rows] == list(AGENTS)
    for row in rows:
        assert row["built_in_status"] == "draft"  # nothing has run on a real model yet
        assert row["summary"]["status"] == "active"  # the local seed switched it on
        assert row["settings"] == {"status": "active", "provider": "test"}


async def test_agent_detail_shows_the_prompt_and_the_guardrails_that_do_not_exist_yet(
    api: httpx.AsyncClient,
) -> None:
    detail = (await api.get("/admin/agents/savings-advisor", headers=as_user(PLATFORM))).json()
    assert detail["prompt"]["text"] and len(detail["prompt"]["sha256"]) == 64
    assert detail["spec"]["config"]["model"]["provider"] == "test"
    assert detail["built_in_spec"]["config"]["model"]["provider"] == "google-gemini"
    assert detail["guardrails_not_implemented"] == []
    assert (await api.get("/admin/agents/nope", headers=as_user(PLATFORM))).status_code == 404


async def test_settings_are_validated_stored_applied_and_audited(api: httpx.AsyncClient) -> None:
    platform = as_user(PLATFORM)
    body = {"status": "active", "provider": "test", "temperature": 0.3, "max_iterations": 3}
    put = await api.put("/admin/agents/energy-analyst/settings", json=body, headers=platform)
    assert put.status_code == 200
    spec = put.json()["spec"]["config"]
    assert spec["temperature"] == 0.3 and spec["max_iterations"] == 3
    again = (await api.get("/admin/agents/energy-analyst", headers=platform)).json()
    assert again["settings"] == body
    audit = (await api.get("/admin/audit", headers=platform)).json()
    changed = [a for a in audit if a["action"] == "agent.settings_changed"]
    assert changed and changed[0]["detail"]["to"] == body
    assert changed[0]["detail"]["from"] == {"status": "active", "provider": "test"}
    assert changed[0]["actor"] == PLATFORM and changed[0]["target"] == "energy-analyst"


@pytest.mark.parametrize(
    "body",
    [
        {"temperature": 5},
        {"max_iterations": 0},
        {"max_iterations": 9},
        {"timeout_seconds": 0},
        {"provider": "openai"},
        {"status": "live"},
        {"blocklist_terms": [""]},
        {"blocklist_terms": ["x" * 101]},
        {"content_safety_enabled": True},  # not implemented, so it cannot be switched on here
        {"prompt_file": "energy_analyst/v9.md"},  # the prompt is code, not a setting
    ],
)
async def test_settings_that_do_not_validate_are_refused_and_change_nothing(
    api: httpx.AsyncClient, body: dict
) -> None:
    platform = as_user(PLATFORM)
    before = (await api.get("/admin/agents/energy-analyst", headers=platform)).json()["settings"]
    response = await api.put("/admin/agents/energy-analyst/settings", json=body, headers=platform)
    assert response.status_code == 422
    after = (await api.get("/admin/agents/energy-analyst", headers=platform)).json()["settings"]
    assert after == before


async def test_a_blocked_term_stops_a_customers_question_before_any_model_is_called(
    api: httpx.AsyncClient,
) -> None:
    settings = {
        "status": "active",
        "provider": "test",
        "blocklist_enabled": True,
        "blocklist_terms": ["bananas"],
    }
    put = await api.put(
        "/admin/agents/energy-analyst/settings", json=settings, headers=as_user(PLATFORM)
    )
    assert put.status_code == 200
    run = await api.post(
        "/agents/energy-analyst/runs",
        json={"site": "demo-hotel", "question": "Why do bananas matter for demand?"},
        headers=as_user(MANAGER),
    )
    record = run.json()
    assert run.status_code == 200 and record["status"] == "failed"
    assert record["error_kind"] == "blocklist_input" and record["attempts"] == 0


async def test_switching_an_agent_off_and_back_on_reaches_customers(
    api: httpx.AsyncClient,
) -> None:
    platform = as_user(PLATFORM)
    off = {"status": "disabled", "provider": "test"}
    await api.put("/admin/agents/report-writer/settings", json=off, headers=platform)
    listed = (await api.get("/agents", headers=as_user(MANAGER))).json()
    assert "report-writer" not in [a["id"] for a in listed]
    on = {"status": "active", "provider": "test"}
    await api.put("/admin/agents/report-writer/settings", json=on, headers=platform)
    listed = (await api.get("/agents", headers=as_user(MANAGER))).json()
    assert "report-writer" in [a["id"] for a in listed]


async def test_resetting_goes_back_to_the_built_in_draft_which_customers_cannot_run(
    api: httpx.AsyncClient,
) -> None:
    reset = await api.delete("/admin/agents/savings-advisor/settings", headers=as_user(PLATFORM))
    assert reset.status_code == 200
    assert reset.json()["settings"] == {} and reset.json()["summary"]["status"] == "draft"
    run = await api.post("/agents/savings-advisor/runs", json={}, headers=as_user(MANAGER))
    assert run.status_code == 409


async def test_the_playground_tries_an_agent_on_demo_data_even_when_it_is_off(
    api: httpx.AsyncClient,
) -> None:
    platform = as_user(PLATFORM)
    await api.delete("/admin/agents/savings-advisor/settings", headers=platform)
    response = await api.post(
        "/admin/agents/savings-advisor/playground", json={"site": "demo-hotel"}, headers=platform
    )
    record = response.json()
    assert response.status_code == 200 and record["status"] == "succeeded"
    assert record["model_provider"] == "test-adapter"
    # It touched no customer's data and is stored with no customer's runs.
    assert (await api.get("/runs", headers=as_user(MANAGER))).json() == []
    audit = (await api.get("/admin/audit", headers=platform)).json()
    assert "agent.playground_run" in [a["action"] for a in audit]


async def test_the_playground_reports_a_missing_provider_key(api: httpx.AsyncClient) -> None:
    response = await api.post(
        "/admin/agents/savings-advisor/playground",
        json={"site": "demo-hotel", "provider": "gemini"},
        headers=as_user(PLATFORM),
    )
    assert response.status_code == 503 and "GEMINI_API_KEY" in response.json()["detail"]
    providers = (await api.get("/admin/providers", headers=as_user(PLATFORM))).json()
    assert [p["id"] for p in providers] == ["test", "gemini", "nebius"]


# ------------------------------------------------------------------ organisations


async def test_the_platform_creates_an_organisation_whose_admin_can_then_sign_in(
    api: httpx.AsyncClient,
) -> None:
    platform = as_user(PLATFORM)
    body = {
        "name": "Acme Energy",
        "slug": "acme-energy",
        "admin_email": "Boss@Acme.example",
        "admin_name": "Ada Acme",
    }
    created = await api.post("/admin/organizations", json=body, headers=platform)
    assert created.status_code == 201 and created.json()["people"] == 1
    me = (await api.get("/me", headers=as_user("boss@acme.example"))).json()
    assert me["role"] == "org_admin" and me["organisation"] == "Acme Energy" and me["sites"] == []
    names = [o["name"] for o in (await api.get("/admin/organizations", headers=platform)).json()]
    assert "Acme Energy" in names
    # The platform admin made the account but cannot read anything inside it.
    assert (await api.get("/sites", headers=platform)).status_code == 403
    audit = (await api.get("/admin/audit", headers=platform)).json()
    assert "organization.created" in [a["action"] for a in audit]


async def test_organisation_names_and_emails_must_be_unique_and_well_formed(
    api: httpx.AsyncClient,
) -> None:
    platform = as_user(PLATFORM)
    good = {"name": "Acme", "slug": "acme", "admin_email": "a@acme.example", "admin_name": "A"}
    assert (await api.post("/admin/organizations", json=good, headers=platform)).status_code == 201
    same_slug = {**good, "admin_email": "b@acme.example"}
    assert (
        await api.post("/admin/organizations", json=same_slug, headers=platform)
    ).status_code == 409
    taken_email = {**good, "slug": "acme-two", "admin_email": ADMIN}
    assert (
        await api.post("/admin/organizations", json=taken_email, headers=platform)
    ).status_code == 409
    for bad in ({"slug": "Not A Slug"}, {"admin_email": "no-at-sign"}):
        response = await api.post(
            "/admin/organizations",
            json={**good, "slug": "other", "admin_email": "c@acme.example", **bad},
            headers=platform,
        )
        assert response.status_code == 422, bad


async def test_switching_an_organisation_off_locks_its_people_out(api: httpx.AsyncClient) -> None:
    platform = as_user(PLATFORM)
    orgs = (await api.get("/admin/organizations", headers=platform)).json()
    northside = next(o for o in orgs if o["slug"] == "northside-health")
    assert (await api.get("/sites", headers=as_user(NORTH))).status_code == 200
    off = await api.patch(
        f"/admin/organizations/{northside['id']}", json={"is_active": False}, headers=platform
    )
    assert off.status_code == 200 and off.json()["is_active"] is False
    assert (await api.get("/sites", headers=as_user(NORTH))).status_code == 403
    on = {"is_active": True}
    await api.patch(f"/admin/organizations/{northside['id']}", json=on, headers=platform)
    assert (await api.get("/sites", headers=as_user(NORTH))).status_code == 200
    missing = "00000000-0000-0000-0000-000000000000"
    gone = await api.patch(f"/admin/organizations/{missing}", json=on, headers=platform)
    assert gone.status_code == 404


async def test_the_platform_sees_an_organisations_people_but_not_their_data(
    api: httpx.AsyncClient,
) -> None:
    platform = as_user(PLATFORM)
    orgs = (await api.get("/admin/organizations", headers=platform)).json()
    harbour = next(o for o in orgs if o["slug"] == "harbour-hotels")
    people = (await api.get(f"/admin/organizations/{harbour['id']}/users", headers=platform)).json()
    assert {p["email"] for p in people} == {ADMIN, MANAGER, TECH, VIEWER}
    assert set(people[0]) == {"id", "email", "name", "role", "is_active"}


# ------------------------------------------------------------------ usage and audit


async def test_usage_is_counts_only_and_split_by_organisation(api: httpx.AsyncClient) -> None:
    for _ in range(2):
        await api.post("/agents/savings-advisor/runs", json={}, headers=as_user(MANAGER))
    await api.post("/agents/energy-analyst/runs", json={}, headers=as_user(MANAGER))  # a 422
    report = (await api.get("/admin/usage", headers=as_user(PLATFORM))).json()
    assert report["runs"] == 2 and report["failed"] == 0
    assert {line["organisation"] for line in report["lines"]} == {"Harbour Hotels"}
    assert {line["agent"] for line in report["lines"]} == {"savings-advisor"}
    assert set(report["lines"][0]) == {
        "organisation",
        "agent",
        "day",
        "runs",
        "failed",
        "input_tokens",
        "output_tokens",
    }
    own = (await api.get("/usage", headers=as_user(VIEWER))).json()
    assert own["runs"] == 2
    other = (await api.get("/usage", headers=as_user(NORTH))).json()
    assert other["runs"] == 0 and other["lines"] == []


async def test_only_the_platform_scope_can_call_the_usage_function(seeded: Database) -> None:
    manager = await authenticate(
        seeded, Claims(subject=MANAGER, email=MANAGER, name=None, external_id=None)
    )
    since = datetime.now(UTC) - timedelta(days=30)
    async with seeded.session(manager.scope) as session:
        assert await repo.platform_usage(session, since) == []
    async with seeded.session(Scope()) as session:
        assert await repo.platform_usage(session, since) == []


async def test_the_organisations_audit_log_is_for_administrators_and_stays_inside(
    api: httpx.AsyncClient,
) -> None:
    await api.post("/agents/savings-advisor/runs", json={}, headers=as_user(MANAGER))
    assert (await api.get("/audit", headers=as_user(MANAGER))).status_code == 403
    mine = (await api.get("/audit", headers=as_user(ADMIN))).json()
    assert [e["action"] for e in mine] == ["agent.run"] and mine[0]["actor"] == MANAGER
    assert (await api.get("/audit", headers=as_user(NORTH))).json() == []
    platform = (await api.get("/admin/audit", headers=as_user(PLATFORM))).json()
    assert "agent.run" not in [e["action"] for e in platform]  # platform rows only


# ------------------------------------------------------------------ people and sites


async def status(
    api: httpx.AsyncClient, method: str, path: str, user: str, body: dict | None = None
) -> int:
    response = await api.request(method, path, json=body, headers=as_user(user))
    return response.status_code


async def person_id(api: httpx.AsyncClient, email: str) -> str:
    people = (await api.get("/users", headers=as_user(ADMIN))).json()
    return next(p["id"] for p in people if p["email"] == email)


async def test_an_admin_lists_only_their_own_people_and_nobody_else_may(
    api: httpx.AsyncClient,
) -> None:
    people = (await api.get("/users", headers=as_user(ADMIN))).json()
    assert {p["email"] for p in people} == {ADMIN, MANAGER, TECH, VIEWER}
    tech = next(p for p in people if p["email"] == TECH)
    assert tech["sites"] == ["demo-hotel"]
    assert next(p for p in people if p["email"] == VIEWER)["sites"] is None
    assert (await api.get("/users", headers=as_user(MANAGER))).status_code == 403
    assert (await api.get("/users", headers=as_user(PLATFORM))).status_code == 403
    north = (await api.get("/users", headers=as_user(NORTH))).json()
    assert {p["email"] for p in north} == {NORTH, "manager@northside.example"}


async def test_adding_a_person_limited_to_a_site(api: httpx.AsyncClient) -> None:
    body = {
        "email": "New.Hand@Harbour.example",
        "name": "Nell Hand",
        "role": "technician",
        "sites": ["demo-office"],
    }
    created = await api.post("/users", json=body, headers=as_user(ADMIN))
    assert created.status_code == 201 and created.json()["sites"] == ["demo-office"]
    me = (await api.get("/me", headers=as_user("new.hand@harbour.example"))).json()
    assert [s["slug"] for s in me["sites"]] == ["demo-office"] and me["role"] == "technician"
    forbidden = await api.get(
        "/sites/demo-hotel/summary", headers=as_user("new.hand@harbour.example")
    )
    assert forbidden.status_code == 404
    audit = (await api.get("/audit", headers=as_user(ADMIN))).json()
    assert audit[0]["action"] == "user.added"


async def test_adding_a_person_is_checked(api: httpx.AsyncClient) -> None:
    admin = as_user(ADMIN)
    base = {"email": "x@harbour.example", "name": "X", "role": "viewer"}
    assert (
        await api.post("/users", json={**base, "role": "platform_admin"}, headers=admin)
    ).status_code == 422
    assert (
        await api.post("/users", json={**base, "email": "nope"}, headers=admin)
    ).status_code == 422
    assert (
        await api.post("/users", json={**base, "sites": ["no-such"]}, headers=admin)
    ).status_code == 422
    assert (
        await api.post("/users", json={**base, "email": NORTH}, headers=admin)
    ).status_code == 409
    assert (
        await api.post("/users", json={**base, "org_id": "x"}, headers=admin)
    ).status_code == 422
    assert await status(api, "POST", "/users", MANAGER, base) == 403
    # A site of another organisation is just as unknown.
    assert await status(api, "POST", "/users", ADMIN, {**base, "sites": ["demo-clinic"]}) == 422


async def test_changing_a_person_takes_effect_at_once(api: httpx.AsyncClient) -> None:
    admin = as_user(ADMIN)
    tech_id = await person_id(api, TECH)
    changed = await api.patch(f"/users/{tech_id}", json={"sites": ["demo-office"]}, headers=admin)
    assert changed.json()["sites"] == ["demo-office"]
    me = (await api.get("/me", headers=as_user(TECH))).json()
    assert [s["slug"] for s in me["sites"]] == ["demo-office"]
    await api.patch(f"/users/{tech_id}", json={"all_sites": True}, headers=admin)
    assert len((await api.get("/me", headers=as_user(TECH))).json()["sites"]) == 2
    await api.patch(f"/users/{tech_id}", json={"role": "viewer"}, headers=admin)
    assert (
        await api.post("/agents/savings-advisor/runs", json={}, headers=as_user(TECH))
    ).status_code == 403
    await api.patch(f"/users/{tech_id}", json={"is_active": False}, headers=admin)
    assert (await api.get("/me", headers=as_user(TECH))).status_code == 403


async def test_nobody_can_change_their_own_role_or_switch_themselves_off(
    api: httpx.AsyncClient,
) -> None:
    me = await person_id(api, ADMIN)
    admin = as_user(ADMIN)
    assert (
        await api.patch(f"/users/{me}", json={"role": "viewer"}, headers=admin)
    ).status_code == 409
    assert (
        await api.patch(f"/users/{me}", json={"is_active": False}, headers=admin)
    ).status_code == 409
    assert (await api.get("/me", headers=admin)).json()["role"] == "org_admin"


async def test_an_admin_cannot_reach_a_person_of_another_organisation(
    api: httpx.AsyncClient,
) -> None:
    people = (await api.get("/users", headers=as_user(NORTH))).json()
    victim = next(p["id"] for p in people if p["email"] == "manager@northside.example")
    response = await api.patch(
        f"/users/{victim}", json={"is_active": False}, headers=as_user(ADMIN)
    )
    assert response.status_code == 404
    assert (await api.get("/me", headers=as_user("manager@northside.example"))).status_code == 200


async def test_adding_a_site_is_for_admins_and_stays_inside_the_organisation(
    api: httpx.AsyncClient,
) -> None:
    body = {"slug": "north-wing", "name": "North Wing"}
    assert (await api.post("/sites", json=body, headers=as_user(MANAGER))).status_code == 403
    assert (await api.post("/sites", json=body, headers=as_user(ADMIN))).status_code == 201
    assert (await api.post("/sites", json=body, headers=as_user(ADMIN))).status_code == 409
    bad = {"slug": "North Wing", "name": "x"}
    assert (await api.post("/sites", json=bad, headers=as_user(ADMIN))).status_code == 422
    admin_sites = [s["slug"] for s in (await api.get("/sites", headers=as_user(ADMIN))).json()]
    assert "north-wing" in admin_sites
    north_sites = [s["slug"] for s in (await api.get("/sites", headers=as_user(NORTH))).json()]
    assert north_sites == ["demo-clinic"]
    tech_sites = [s["slug"] for s in (await api.get("/sites", headers=as_user(TECH))).json()]
    assert tech_sites == ["demo-hotel"]
    # The same short name is free in another organisation.
    assert (await api.post("/sites", json=body, headers=as_user(NORTH))).status_code == 201
    assert AGENTS  # keeps the import honest
