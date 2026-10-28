"""The assistant: one box for questions. The supervisor reads the question and sends it to the
right agent or capability for the caller, on one of the caller's own sites."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from energyops.agents.registry import RunInputs
from energyops.agents.runs import RunRecord
from energyops.agents.supervisor import (
    EventBrief,
    Supervisor,
    SupervisorRequest,
    SupervisorTools,
    ToolRefused,
)
from energyops.anomalies import detect_anomalies
from energyops.api.providers import ProviderUnavailable
from energyops.calculations import EnergyDataError
from energyops.db import repo
from energyops.security.permissions import Permission
from energyops.security.principal import Principal
from energyops.service import tickets_service
from energyops.service.data import SiteData, SiteNotFound, load_site, sources_for
from energyops.service.deps import needs, state_of
from energyops.service.routes_agents import resolve_agent, run_for_caller
from energyops.service.state import ServiceState

router = APIRouter()
Asker = Annotated[Principal, Depends(needs(Permission.ASK_ASSISTANT))]


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=500)
    site: str | None = Field(default=None, max_length=64)


class AskResponse(BaseModel):
    run: RunRecord
    # The record of the agent the supervisor sent the question to, when there was one.
    child: RunRecord | None = None


async def _events(site_data: SiteData) -> list[EventBrief]:
    try:
        found = detect_anomalies(site_data.readings).anomalies
    except EnergyDataError:
        return []
    return [EventBrief(id=e.id, kind=e.kind, start=e.start.isoformat()) for e in found]


def tools_for(
    request: Request, state: ServiceState, principal: Principal, site_data: SiteData
) -> tuple[SupervisorTools, dict[str, RunRecord]]:
    """The supervisor's hands for this person and this site, and a place to collect the records
    of the agents it calls."""
    children: dict[str, RunRecord] = {}

    async def run_agent(agent_id: str, inputs: RunInputs) -> RunRecord:
        try:
            entry, spec, provider = await resolve_agent(request, principal, agent_id)
            record = await run_for_caller(
                state, principal, entry, spec, provider, site_data, inputs
            )
        except HTTPException as exc:
            raise ToolRefused(str(exc.detail)) from None
        children[record.run_id] = record
        return record

    async def events() -> list[EventBrief]:
        return await _events(site_data)

    async def draft_ticket(anomaly_id: str) -> dict[str, Any]:
        try:
            ticket_id = await tickets_service.create_for_event(
                state, principal, site_data, anomaly_id
            )
        except HTTPException as exc:
            raise ToolRefused(str(exc.detail)) from None
        async with state.db.session(principal.scope) as session:
            found = await repo.get_ticket(session, principal.org(), ticket_id, principal.site_ids)
        assert found is not None
        ticket, _ = found
        return {"ticket_id": str(ticket.id), "title": ticket.title, "status": ticket.status}

    tools = SupervisorTools(
        permissions=principal.permissions,
        run_agent=run_agent,
        events=events,
        draft_ticket=draft_ticket,
        site=site_data.site.slug,
    )
    return tools, children


@router.post("/assistant/ask")
async def ask(body: AskRequest, principal: Asker, request: Request) -> AskResponse:
    state = state_of(request)
    entry, spec, provider = await resolve_agent(request, principal, "supervisor")
    try:
        site_data = await load_site(state.db, principal, body.site)
    except SiteNotFound:
        raise HTTPException(status_code=404, detail="No site with that name.") from None
    run_request = entry.make_request(RunInputs(site=site_data.site.slug, question=body.question))
    assert isinstance(run_request, SupervisorRequest)
    tools, children = tools_for(request, state, principal, site_data)
    try:
        client = state.client_factory(
            provider,
            spec.config.model.name,
            state.keys(),
            entry.test_reply(sources_for(site_data), run_request),
        )
    except ProviderUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    record = await Supervisor(spec, client, tools).run(run_request)
    org = principal.org()
    async with state.db.session(principal.scope) as session:
        await repo.save_run(session, org, principal.user_id, "supervisor", record)
        await repo.audit(
            session,
            org_id=org,
            actor_id=principal.user_id,
            actor_email=principal.email,
            action="assistant.ask",
            target_type="run",
            target_id=record.run_id,
            detail={
                "site": site_data.site.slug,
                "status": record.status.value,
                "route": (record.output or {}).get("route"),
                "outcome": (record.output or {}).get("outcome"),
            },
        )
    child_id = (record.output or {}).get("child_run_id")
    return AskResponse(run=record, child=children.get(child_id) if child_id else None)
