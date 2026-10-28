"""Drafting tickets and recording decisions. Used by the ticket routes and by the assistant."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from energyops.anomalies import detect_anomalies
from energyops.calculations import EnergyDataError
from energyops.db import repo
from energyops.db.models import Site, Ticket
from energyops.graphs import tickets as approval
from energyops.mcp_servers.schema import AnomalyListModel
from energyops.security.principal import Principal
from energyops.service.data import SiteData
from energyops.service.state import ServiceState


async def _graph(state: ServiceState) -> Any:
    assert state.checkpointing is not None
    return approval.build_approval_graph(await state.checkpointing.saver())


async def create_for_event(
    state: ServiceState, principal: Principal, data: SiteData, anomaly_id: str
) -> uuid.UUID:
    """Draft a ticket for an event the detector found and pause it for approval. Returns the
    ticket id. Raises HTTPException with a message that is safe to show."""
    org = principal.org()
    try:
        events = AnomalyListModel.from_result(detect_anomalies(data.readings))
    except EnergyDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    event = next((e for e in events.anomalies if e.id == anomaly_id), None)
    if event is None:
        raise HTTPException(
            status_code=404, detail=f"There is no event {anomaly_id} at {data.site.name}."
        )
    graph = await _graph(state)
    ticket_id = uuid.uuid4()
    async with state.db.session(principal.scope) as session:
        existing = await repo.pending_ticket_for_event(session, org, data.site.id, anomaly_id)
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail=f"A ticket for {anomaly_id} is already waiting for approval.",
            )
        draft = await approval.start(graph, ticket_id, data.site.name, event)
        await repo.create_ticket(
            session,
            org,
            ticket_id=ticket_id,
            site_id=data.site.id,
            created_by=principal.user_id,
            anomaly_id=anomaly_id,
            draft=draft,
        )
        await repo.audit(
            session,
            org_id=org,
            actor_id=principal.user_id,
            actor_email=principal.email,
            action="ticket.drafted",
            target_type="ticket",
            target_id=str(ticket_id),
            detail={"site": data.site.slug, "event": anomaly_id},
        )
    return ticket_id


async def decide(
    state: ServiceState,
    principal: Principal,
    ticket_id: uuid.UUID,
    *,
    approve: bool,
    note: str | None,
    priority: str | None,
) -> tuple[Ticket, Site]:
    """Record a person's decision on a waiting ticket, by resuming its graph."""
    org = principal.org()
    graph = await _graph(state)
    async with state.db.session(principal.scope) as session:
        found = await repo.get_ticket(session, org, ticket_id, principal.site_ids, lock=True)
        if found is None:
            raise HTTPException(status_code=404, detail="No such ticket.")
        ticket, site = found
        if ticket.status != "pending_approval":
            raise HTTPException(status_code=409, detail="This ticket has already been decided.")
        if not await approval.is_waiting(graph, ticket_id):
            raise HTTPException(
                status_code=409, detail="This ticket is no longer waiting for a decision."
            )
        try:
            outcome = await approval.decide(
                graph,
                ticket_id,
                approve=approve,
                decided_by=principal.user_id,
                note=note,
                priority=priority,
            )
        except approval.InvalidDecision as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        ticket.status = "approved" if outcome["approved"] else "rejected"
        ticket.priority = outcome["priority"]
        ticket.decided_by = principal.user_id
        ticket.decided_at = datetime.now(UTC)
        ticket.decision_note = outcome["note"]
        await repo.audit(
            session,
            org_id=org,
            actor_id=principal.user_id,
            actor_email=principal.email,
            action=f"ticket.{ticket.status}",
            target_type="ticket",
            target_id=str(ticket_id),
            detail={"site": site.slug, "priority": ticket.priority},
        )
        await session.flush()
    return ticket, site
