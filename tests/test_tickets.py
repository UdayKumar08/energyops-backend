"""Ticket drafting and approval: the graph on its own, then the API on a real PostgreSQL."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import text

from energyops.db import repo
from energyops.db.session import Database, Scope
from energyops.graphs import tickets as approval
from energyops.mcp_servers.schema import AnomalyModel
from energyops.security.auth import authenticate
from energyops.security.tokens import Claims
from energyops.tickets import draft_from_event
from service_helpers import ADMIN, MANAGER, NORTH, TECH, VIEWER, as_user, make_app

NORTH_MANAGER = "manager@northside.example"


def event(kind: str = "spike", **changes: object) -> AnomalyModel:
    base = {
        "id": "A1",
        "kind": kind,
        "start": "2026-03-04T02:00:00+00:00",
        "end": "2026-03-04T04:00:00+00:00",
        "intervals": 8,
        "peak_at": "2026-03-04T02:30:00+00:00",
        "observed_kw_at_peak": 50.0,
        "baseline_kw_at_peak": 15.0,
        "peak_deviation_kw": 35.0 if kind == "spike" else -15.0,
        "energy_difference_kwh": 70.0 if kind == "spike" else -30.0,
    }
    return AnomalyModel.model_validate({**base, **changes})


# ------------------------------------------------------------------ the draft


def test_a_draft_states_measured_figures_and_never_a_cause() -> None:
    draft = draft_from_event("Northside Clinic", event())
    assert "Northside Clinic" in draft.title and "high" in draft.title
    assert draft.priority == "medium"
    joined = "\n".join(draft.evidence)
    assert "50.0 kW" in joined and "15.0 kW" in joined and "70.0 kWh more" in joined
    assert "does not know the cause" in draft.description
    for word in ("because", "caused by", "the fault is", "faulty"):
        assert word not in draft.description.lower()


def test_a_drop_is_worded_as_a_drop_with_meter_checks_first() -> None:
    draft = draft_from_event("Northside Clinic", event("drop"))
    assert "low" in draft.title and "30.0 kWh less" in "\n".join(draft.evidence)
    assert "meter" in draft.description.split("Suggested checks:")[1].lower()


# ------------------------------------------------------------------ the graph on its own


async def test_the_graph_pauses_then_applies_a_decision() -> None:
    graph = approval.build_approval_graph(InMemorySaver())
    ticket_id = uuid.uuid4()
    draft = await approval.start(graph, ticket_id, "Northside Clinic", event())
    assert draft["priority"] == "medium" and await approval.is_waiting(graph, ticket_id)
    user = uuid.uuid4()
    outcome = await approval.decide(
        graph, ticket_id, approve=True, decided_by=user, note="Go", priority="high"
    )
    assert outcome == {"approved": True, "priority": "high", "note": "Go", "decided_by": str(user)}
    assert not await approval.is_waiting(graph, ticket_id)


async def test_a_rejection_keeps_the_drafts_priority() -> None:
    graph = approval.build_approval_graph(InMemorySaver())
    ticket_id = uuid.uuid4()
    await approval.start(graph, ticket_id, "Northside Clinic", event())
    outcome = await approval.decide(
        graph, ticket_id, approve=False, decided_by=uuid.uuid4(), note=None, priority=None
    )
    assert outcome["approved"] is False and outcome["priority"] == "medium"


async def test_a_decision_the_graph_does_not_accept_is_refused() -> None:
    graph = approval.build_approval_graph(InMemorySaver())
    ticket_id = uuid.uuid4()
    await approval.start(graph, ticket_id, "Northside Clinic", event())
    with pytest.raises(approval.InvalidDecision):
        await approval.decide(
            graph, ticket_id, approve=True, decided_by=uuid.uuid4(), note=None, priority="urgent"
        )


# ------------------------------------------------------------------ the API


async def draft(api: httpx.AsyncClient, user: str = NORTH_MANAGER, **body: str) -> httpx.Response:
    payload = {"site": "demo-clinic", "anomaly_id": "A1", **body}
    return await api.post("/tickets", json=payload, headers=as_user(user))


async def add_event_at_office(db: Database) -> None:
    """Give the Harbour office a clear after-hours spike, so it has an event of its own."""
    admin = await authenticate(db, Claims(subject=ADMIN, email=ADMIN, name=None, external_id=None))
    org = admin.org()
    async with db.session(admin.scope) as session:
        site = await repo.get_site(session, org, "demo-office", None)
        assert site is not None
        meter = await repo.primary_meter(session, org, site.id)
        assert meter is not None
        readings = await repo.load_readings(session, org, meter, label="demo-office", days=28)
        start = datetime(2026, 3, 11, 2, tzinfo=UTC)
        changed = [
            replace(r, power_kw=r.power_kw + 60.0)
            if start.hour <= r.timestamp.hour < 4 and r.timestamp.date() == start.date()
            else r
            for r in readings
        ]
        await repo.upsert_readings(session, org, meter.id, changed)


async def test_drafting_a_ticket_waits_for_approval(api: httpx.AsyncClient) -> None:
    response = await draft(api)
    assert response.status_code == 201, response.text
    ticket = response.json()
    assert ticket["status"] == "pending_approval" and ticket["awaiting_approval"] is True
    assert ticket["site"] == "demo-clinic" and ticket["created_by"] == "Noor Northside"
    assert "outside ticketing system" in ticket["note"]
    assert ticket["decided_by"] is None and ticket["evidence"]
    listed = (await api.get("/tickets?status=pending_approval", headers=as_user(NORTH))).json()
    assert [t["id"] for t in listed] == [ticket["id"]]


async def test_a_second_draft_for_the_same_event_is_refused_while_one_is_waiting(
    api: httpx.AsyncClient,
) -> None:
    assert (await draft(api)).status_code == 201
    again = await draft(api)
    assert again.status_code == 409 and "already waiting" in again.json()["detail"]


async def test_drafting_checks_the_event_the_site_and_the_format(api: httpx.AsyncClient) -> None:
    assert (await draft(api, anomaly_id="A99")).status_code == 404
    assert (await draft(api, anomaly_id="a1")).status_code == 422
    assert (await draft(api, anomaly_id="1; drop table")).status_code == 422
    assert (await draft(api, site="demo-hotel")).status_code == 404  # another organisation's


async def test_only_a_manager_or_above_decides_and_the_decision_is_recorded(
    api: httpx.AsyncClient,
) -> None:
    ticket = (await draft(api, user=NORTH_MANAGER)).json()
    body = {"approve": True, "note": "Please check the HVAC schedule.", "priority": "high"}
    viewer_like = await api.post(
        f"/tickets/{ticket['id']}/decision", json=body, headers=as_user(VIEWER)
    )
    assert viewer_like.status_code == 403
    done = await api.post(f"/tickets/{ticket['id']}/decision", json=body, headers=as_user(NORTH))
    assert done.status_code == 200
    result = done.json()
    assert result["status"] == "approved" and result["priority"] == "high"
    assert result["decided_by"] == "Nia Northside" and result["awaiting_approval"] is False
    assert result["decision_note"] == "Please check the HVAC schedule."
    again = await api.post(f"/tickets/{ticket['id']}/decision", json=body, headers=as_user(NORTH))
    assert again.status_code == 409


async def test_a_rejection_is_recorded_too(api: httpx.AsyncClient) -> None:
    ticket = (await draft(api)).json()
    done = await api.post(
        f"/tickets/{ticket['id']}/decision", json={"approve": False}, headers=as_user(NORTH)
    )
    assert done.json()["status"] == "rejected" and done.json()["priority"] == "medium"
    approved = (await api.get("/tickets?status=approved", headers=as_user(NORTH))).json()
    assert approved == []
    # A rejected ticket frees the event for a fresh draft.
    assert (await draft(api)).status_code == 201


async def test_an_unusable_decision_is_a_422_and_the_ticket_stays_waiting(
    api: httpx.AsyncClient,
) -> None:
    ticket = (await draft(api)).json()
    for body in (
        {"approve": True, "priority": "urgent"},
        {"approve": "yes"},
        {"approve": 1},
        {"note": "x"},
    ):
        response = await api.post(
            f"/tickets/{ticket['id']}/decision", json=body, headers=as_user(NORTH)
        )
        assert response.status_code == 422, body
    still = (await api.get(f"/tickets/{ticket['id']}", headers=as_user(NORTH))).json()
    assert still["status"] == "pending_approval"


async def test_a_ticket_waiting_for_approval_survives_a_restart(seeded: Database) -> None:
    """The graph's state is in PostgreSQL, so a brand new server (new connection pools, nothing
    in memory) can finish what the first one started."""
    first = make_app(seeded)
    transport = httpx.ASGITransport(app=first)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        ticket = (await draft(client)).json()
    await first.state.service.close()
    second = make_app(seeded)
    transport = httpx.ASGITransport(app=second)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            done = await client.post(
                f"/tickets/{ticket['id']}/decision",
                json={"approve": True},
                headers=as_user(NORTH),
            )
        assert done.status_code == 200 and done.json()["status"] == "approved"
    finally:
        await second.state.service.close()


async def test_the_saved_state_is_in_the_checkpoint_tables(
    api: httpx.AsyncClient, owner_db: Database
) -> None:
    ticket = (await draft(api)).json()
    async with owner_db.session(Scope()) as session:
        saved = (
            await session.execute(
                text("select count(*) from checkpoints where thread_id = :t"), {"t": ticket["id"]}
            )
        ).scalar_one()
    assert saved >= 1


async def test_a_ticket_whose_saved_state_is_gone_cannot_be_decided(
    api: httpx.AsyncClient, owner_db: Database
) -> None:
    ticket = (await draft(api)).json()
    async with owner_db.session(Scope()) as session:
        for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            await session.execute(
                text(f"delete from {table} where thread_id = :t"), {"t": ticket["id"]}
            )
    done = await api.post(
        f"/tickets/{ticket['id']}/decision", json={"approve": True}, headers=as_user(NORTH)
    )
    assert done.status_code == 409 and "no longer waiting" in done.json()["detail"]


async def test_another_organisation_cannot_see_or_decide_a_ticket(api: httpx.AsyncClient) -> None:
    ticket = (await draft(api)).json()
    for user in (ADMIN, MANAGER):
        assert (await api.get(f"/tickets/{ticket['id']}", headers=as_user(user))).status_code == 404
        decision = await api.post(
            f"/tickets/{ticket['id']}/decision", json={"approve": True}, headers=as_user(user)
        )
        assert decision.status_code == 404
        assert (await api.get("/tickets", headers=as_user(user))).json() == []
    still = (await api.get(f"/tickets/{ticket['id']}", headers=as_user(NORTH))).json()
    assert still["status"] == "pending_approval"


async def test_a_person_limited_to_some_sites_does_not_see_tickets_for_other_sites(
    api: httpx.AsyncClient, seeded: Database
) -> None:
    await add_event_at_office(seeded)
    events = (await api.get("/sites/demo-office/anomalies", headers=as_user(MANAGER))).json()
    assert events["anomalies"], "the injected spike should be found"
    event_id = events["anomalies"][0]["id"]
    made = await api.post(
        "/tickets",
        json={"site": "demo-office", "anomaly_id": event_id},
        headers=as_user(MANAGER),
    )
    assert made.status_code == 201, made.text
    ticket_id = made.json()["id"]
    assert (await api.get("/tickets", headers=as_user(TECH))).json() == []
    assert (await api.get(f"/tickets/{ticket_id}", headers=as_user(TECH))).status_code == 404
    seen = await api.get(f"/tickets/{ticket_id}", headers=as_user(VIEWER))
    assert seen.status_code == 200
    # The technician cannot draft for the office either.
    other = await api.post(
        "/tickets",
        json={"site": "demo-office", "anomaly_id": event_id},
        headers=as_user(TECH),
    )
    assert other.status_code == 404


async def test_viewers_can_read_but_not_draft_or_decide(api: httpx.AsyncClient) -> None:
    ticket = (await draft(api)).json()
    assert (await api.get("/tickets", headers=as_user(NORTH))).status_code == 200
    viewer_draft = await api.post(
        "/tickets", json={"site": "demo-hotel", "anomaly_id": "A1"}, headers=as_user(VIEWER)
    )
    assert viewer_draft.status_code == 403
    assert ticket["id"]


async def test_drafting_and_deciding_are_in_the_audit_log(api: httpx.AsyncClient) -> None:
    ticket = (await draft(api)).json()
    await api.post(
        f"/tickets/{ticket['id']}/decision", json={"approve": True}, headers=as_user(NORTH)
    )
    actions = [e["action"] for e in (await api.get("/audit", headers=as_user(NORTH))).json()]
    assert actions[:2] == ["ticket.approved", "ticket.drafted"]
