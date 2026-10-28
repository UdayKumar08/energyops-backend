"""The assistant endpoint: the supervisor acting for a signed-in person on a real PostgreSQL."""

from __future__ import annotations

import httpx

from service_helpers import ADMIN, MANAGER, NORTH, PLATFORM, TECH, VIEWER, as_user

NORTH_MANAGER = "manager@northside.example"


async def ask(
    api: httpx.AsyncClient, user: str, question: str, site: str | None = None
) -> httpx.Response:
    body = {"question": question, **({"site": site} if site else {})}
    return await api.post("/assistant/ask", json=body, headers=as_user(user))


async def test_a_cost_question_goes_to_the_savings_advisor_and_both_runs_are_kept(
    api: httpx.AsyncClient,
) -> None:
    response = await ask(api, MANAGER, "How much could we save on our energy bill?", "demo-hotel")
    assert response.status_code == 200, response.text
    body = response.json()
    out = body["run"]["output"]
    assert body["run"]["status"] == "succeeded" and body["run"]["model_provider"] == "test-adapter"
    assert out["route"] == "savings" and out["outcome"] == "answered"
    assert out["agent_id"] == "savings-advisor" and out["result"]["actions"]
    assert body["child"]["run_id"] == out["child_run_id"]
    assert body["child"]["status"] == "succeeded"
    listed = (await api.get("/runs", headers=as_user(MANAGER))).json()
    assert sorted(r["agent"] for r in listed) == [
        "EnergyOps Savings Advisor",
        "EnergyOps Supervisor",
    ]
    audit = (await api.get("/audit", headers=as_user(ADMIN))).json()
    assistant = [a for a in audit if a["action"] == "assistant.ask"]
    assert assistant[0]["detail"]["route"] == "savings"


async def test_an_energy_question_goes_to_the_analyst(api: httpx.AsyncClient) -> None:
    body = (await ask(api, MANAGER, "What was the peak demand?", "demo-hotel")).json()
    assert body["run"]["output"]["route"] == "analyst"
    assert body["run"]["output"]["outcome"] == "answered"


async def test_an_out_of_scope_request_is_declined_with_a_fixed_message(
    api: httpx.AsyncClient,
) -> None:
    body = (await ask(api, MANAGER, "Write me a poem about winter.")).json()
    out = body["run"]["output"]
    assert out["outcome"] == "declined" and out["route"] == "decline"
    assert out["message"].startswith("I can help with energy use")
    assert body["child"] is None and body["run"]["status"] == "succeeded"


async def test_asking_to_raise_a_ticket_drafts_one_that_waits_for_approval(
    api: httpx.AsyncClient,
) -> None:
    body = (await ask(api, NORTH_MANAGER, "Raise a ticket for event A1.")).json()
    out = body["run"]["output"]
    assert out["outcome"] == "ticket_pending" and out["result"]["status"] == "pending_approval"
    tickets = (await api.get("/tickets?status=pending_approval", headers=as_user(NORTH))).json()
    assert [t["id"] for t in tickets] == [out["result"]["ticket_id"]]
    again = (await ask(api, NORTH_MANAGER, "Raise a ticket for event A1.")).json()
    assert "already waiting" in again["run"]["output"]["message"]


async def test_a_ticket_request_without_an_event_asks_which_one(api: httpx.AsyncClient) -> None:
    out = (await ask(api, NORTH_MANAGER, "Please raise a ticket.")).json()["run"]["output"]
    assert out["outcome"] == "needs_input" and "A1" in out["message"]
    assert (await api.get("/tickets", headers=as_user(NORTH))).json() == []


async def test_a_question_about_an_event_goes_to_the_investigator(api: httpx.AsyncClient) -> None:
    out = (await ask(api, NORTH_MANAGER, "Why did A1 happen?")).json()["run"]["output"]
    assert out["route"] == "investigate" and out["outcome"] == "answered"
    assert out["agent_id"] == "anomaly-investigator"


async def test_a_ticket_for_an_event_that_does_not_exist_is_explained(
    api: httpx.AsyncClient,
) -> None:
    out = (await ask(api, TECH, "Raise a ticket for A7", "demo-hotel")).json()["run"]["output"]
    assert out["outcome"] == "needs_input" and "no event A7" in out["message"]


async def test_the_assistant_stays_inside_the_callers_sites_and_roles(
    api: httpx.AsyncClient,
) -> None:
    other_org = await ask(api, MANAGER, "Costs?", "demo-clinic")
    restricted = await ask(api, TECH, "Costs?", "demo-office")
    assert other_org.status_code == restricted.status_code == 404
    assert other_org.json() == restricted.json()
    assert (await ask(api, VIEWER, "Costs?")).status_code == 403
    assert (await ask(api, PLATFORM, "Costs?")).status_code == 403
    assert (await api.post("/assistant/ask", json={"question": "Costs?"})).status_code == 401


async def test_a_limited_person_is_answered_from_their_own_site_by_default(
    api: httpx.AsyncClient,
) -> None:
    body = (await ask(api, TECH, "How much could we save on our energy bill?")).json()
    assert body["run"]["input"]["site"] == "demo-hotel"
    assert body["child"]["input"]["site"] == "demo-hotel"


async def test_the_request_takes_only_a_question_and_a_site(api: httpx.AsyncClient) -> None:
    for extra in ({"provider": "gemini"}, {"permissions": ["admin_platform"]}, {"role": "x"}):
        response = await api.post(
            "/assistant/ask", json={"question": "Costs?", **extra}, headers=as_user(MANAGER)
        )
        assert response.status_code == 422, extra
    long = await ask(api, MANAGER, "x" * 501)
    assert long.status_code == 422


async def test_a_switched_off_assistant_or_agent_is_explained(api: httpx.AsyncClient) -> None:
    platform = as_user(PLATFORM)
    off = {"status": "disabled", "provider": "test"}
    await api.put("/admin/agents/savings-advisor/settings", json=off, headers=platform)
    out = (await ask(api, MANAGER, "How much could we save?", "demo-hotel")).json()["run"]["output"]
    assert out["outcome"] == "declined" and out["message"] == "This agent is not switched on."
    await api.put("/admin/agents/supervisor/settings", json=off, headers=platform)
    assert (await ask(api, MANAGER, "Costs?")).status_code == 409


async def test_the_supervisor_is_not_listed_or_run_as_a_plain_agent(api: httpx.AsyncClient) -> None:
    listed = (await api.get("/agents", headers=as_user(MANAGER))).json()
    assert "supervisor" not in [a["id"] for a in listed]
    run = await api.post("/agents/supervisor/runs", json={}, headers=as_user(MANAGER))
    assert run.status_code == 400
    admin = (await api.get("/admin/agents", headers=as_user(PLATFORM))).json()
    assert "supervisor" in [a["summary"]["id"] for a in admin]


async def test_the_platform_admin_can_try_the_supervisor_on_demo_data(
    api: httpx.AsyncClient,
) -> None:
    platform = as_user(PLATFORM)
    response = await api.post(
        "/admin/agents/supervisor/playground",
        json={"question": "How much could we save on our energy bill?", "site": "demo-hotel"},
        headers=platform,
    )
    record = response.json()
    assert response.status_code == 200 and record["status"] == "succeeded"
    assert record["output"]["route"] == "savings" and record["output"]["outcome"] == "answered"
    ticket = await api.post(
        "/admin/agents/supervisor/playground",
        json={"question": "Raise a ticket for A1", "site": "demo-clinic"},
        headers=platform,
    )
    assert "need a real site" in ticket.json()["output"]["message"]
    no_question = await api.post("/admin/agents/supervisor/playground", json={}, headers=platform)
    assert no_question.status_code == 422
    assert (await api.get("/runs", headers=as_user(MANAGER))).json() == []
