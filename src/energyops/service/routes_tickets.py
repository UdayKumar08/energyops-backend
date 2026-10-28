"""Tickets: drafted from an event, approved or rejected by a person with the right role."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from energyops.db import repo
from energyops.db.models import Site, Ticket
from energyops.security.permissions import Permission
from energyops.security.principal import Principal
from energyops.service import tickets_service
from energyops.service.data import SiteNotFound, load_site
from energyops.service.deps import needs, state_of

router = APIRouter()
Viewer = Annotated[Principal, Depends(needs(Permission.VIEW_DATA))]
Drafter = Annotated[Principal, Depends(needs(Permission.DRAFT_TICKETS))]
Approver = Annotated[Principal, Depends(needs(Permission.APPROVE_TICKETS))]


class TicketView(BaseModel):
    id: str
    site: str
    site_name: str
    anomaly_id: str | None
    title: str
    description: str
    priority: str
    evidence: list[str]
    status: str
    awaiting_approval: bool
    created_by: str | None
    created_at: str
    decided_by: str | None
    decided_at: str | None
    decision_note: str | None
    note: str


NOTE = "Nothing is sent to an outside ticketing system. This record is the whole ticket."


def view(ticket: Ticket, site: Site, names: dict[uuid.UUID, str]) -> TicketView:
    return TicketView(
        id=str(ticket.id),
        site=site.slug,
        site_name=site.name,
        anomaly_id=ticket.anomaly_id,
        title=ticket.title,
        description=ticket.description,
        priority=ticket.priority,
        evidence=[str(e) for e in ticket.evidence],
        status=ticket.status,
        awaiting_approval=ticket.status == "pending_approval",
        created_by=names.get(ticket.created_by) if ticket.created_by else None,
        created_at=ticket.created_at.isoformat(),
        decided_by=names.get(ticket.decided_by) if ticket.decided_by else None,
        decided_at=ticket.decided_at.isoformat() if ticket.decided_at else None,
        decision_note=ticket.decision_note,
        note=NOTE,
    )


async def _views(
    request: Request, principal: Principal, rows: list[tuple[Ticket, Site]]
) -> list[TicketView]:
    ids = {i for t, _ in rows for i in (t.created_by, t.decided_by) if i is not None}
    async with state_of(request).db.session(principal.scope) as session:
        names = await repo.user_names(session, ids)
    return [view(t, s, names) for t, s in rows]


class NewTicket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site: str = Field(min_length=1, max_length=64)
    anomaly_id: str = Field(pattern=r"^A\d{1,3}$")


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approve: StrictBool  # "yes" or 1 must never count as an approval
    note: str | None = Field(default=None, max_length=500)
    priority: Literal["low", "medium", "high"] | None = None


@router.get("/tickets")
async def list_tickets(
    principal: Viewer,
    request: Request,
    status: Literal["pending_approval", "approved", "rejected"] | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[TicketView]:
    async with state_of(request).db.session(principal.scope) as session:
        rows = await repo.list_tickets(
            session, principal.org(), principal.site_ids, status=status, limit=limit
        )
    return await _views(request, principal, rows)


@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: uuid.UUID, principal: Viewer, request: Request) -> TicketView:
    async with state_of(request).db.session(principal.scope) as session:
        found = await repo.get_ticket(session, principal.org(), ticket_id, principal.site_ids)
    if found is None:
        raise HTTPException(status_code=404, detail="No such ticket.")
    return (await _views(request, principal, [found]))[0]


@router.post("/tickets", status_code=201)
async def draft_ticket(body: NewTicket, principal: Drafter, request: Request) -> TicketView:
    """Draft a ticket for an event at one of the caller's sites. It waits for a manager."""
    state = state_of(request)
    try:
        data = await load_site(state.db, principal, body.site)
    except SiteNotFound:
        raise HTTPException(status_code=404, detail="No site with that name.") from None
    ticket_id = await tickets_service.create_for_event(state, principal, data, body.anomaly_id)
    async with state.db.session(principal.scope) as session:
        found = await repo.get_ticket(session, principal.org(), ticket_id, principal.site_ids)
    assert found is not None
    return (await _views(request, principal, [found]))[0]


@router.post("/tickets/{ticket_id}/decision")
async def decide_ticket(
    ticket_id: uuid.UUID, body: Decision, principal: Approver, request: Request
) -> TicketView:
    ticket, site = await tickets_service.decide(
        state_of(request),
        principal,
        ticket_id,
        approve=body.approve,
        note=body.note,
        priority=body.priority,
    )
    return (await _views(request, principal, [(ticket, site)]))[0]
