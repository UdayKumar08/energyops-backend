"""Listing and running agents for the signed-in person, and reading back their runs."""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from energyops.agents.registry import AGENTS, AgentEntry, InvalidRunInput, RunInputs
from energyops.agents.runs import RunRecord
from energyops.agents.spec import AgentSpec, AgentStatus
from energyops.api.providers import ProviderUnavailable
from energyops.api.views import AgentSummary, RunSummary, run_summary, summarise
from energyops.config import Environment
from energyops.db import repo
from energyops.security.permissions import Permission
from energyops.security.principal import Principal
from energyops.service.agent_config import (
    InvalidOverrides,
    ProviderName,
    effective_spec,
    provider_for,
)
from energyops.service.data import SiteData, SiteNotFound, load_site, sources_for
from energyops.service.deps import needs, state_of
from energyops.service.state import ServiceState

router = APIRouter()


class PlatformRunRequest(BaseModel):
    """What a person may supply. The provider, the model and the way data is read are not here:
    they are set by a platform admin, so a caller cannot pick them."""

    model_config = ConfigDict(extra="forbid")

    question: str | None = Field(default=None, min_length=1, max_length=500)
    audience: str | None = Field(default=None, max_length=32)
    anomaly_id: str | None = Field(default=None, max_length=8)
    site: str | None = Field(default=None, max_length=64)


def entry_for(agent_id: str) -> AgentEntry:
    entry = AGENTS.get(agent_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"No agent called '{agent_id}'.")
    return entry


async def effective(request: Request, principal: Principal, entry: AgentEntry) -> AgentSpec:
    state = state_of(request)
    async with state.db.session(principal.scope) as session:
        overrides = await repo.get_agent_overrides(session)
    try:
        return effective_spec(entry.spec, overrides.get(entry.id))
    except InvalidOverrides as exc:
        raise HTTPException(
            status_code=503, detail=f"This agent's settings are not valid: {exc}"
        ) from None


@router.get("/agents")
async def list_agents(
    principal: Annotated[Principal, Depends(needs(Permission.RUN_AGENTS))], request: Request
) -> list[AgentSummary]:
    """The agents a person can run: only those a platform admin has switched on. The supervisor
    is used through the assistant, not listed here."""
    summaries = []
    for entry in AGENTS.values():
        if not entry.direct:
            continue
        spec = await effective(request, principal, entry)
        if spec.status is AgentStatus.ACTIVE:
            summaries.append(summarise(entry, spec))
    return summaries


async def resolve_agent(
    request: Request, principal: Principal, agent_id: str
) -> tuple[AgentEntry, AgentSpec, ProviderName]:
    """The agent, its settings applied, and the provider it will run on. Raises an
    HTTPException that is safe to show when it cannot run."""
    state = state_of(request)
    entry = entry_for(agent_id)
    spec = await effective(request, principal, entry)
    if spec.status is not AgentStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="This agent is not switched on.")
    provider = provider_for(spec)
    if provider is None or (provider == "test" and state.environment is Environment.PROD):
        raise HTTPException(
            status_code=503, detail="No model is set up for this agent. Ask a platform admin."
        )
    return entry, spec, provider


async def run_for_caller(
    state: ServiceState,
    principal: Principal,
    entry: AgentEntry,
    spec: AgentSpec,
    provider: ProviderName,
    site_data: SiteData,
    inputs: RunInputs,
) -> RunRecord:
    """Run one agent on the caller's data, store the run and write the audit event."""
    data = sources_for(site_data)
    try:
        run_request = entry.make_request(replace(inputs, site=site_data.site.slug))
    except InvalidRunInput as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    try:
        test_reply = entry.test_reply(data, run_request)
    except (LookupError, ValueError):
        test_reply = "{}"  # the agent reports the unusable data itself
    try:
        client = state.client_factory(provider, spec.config.model.name, state.keys(), test_reply)
    except ProviderUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    facts = entry.make_facts(spec, "mcp", data)
    record = await entry.build(spec, client, facts).run(run_request)
    org = principal.org()
    async with state.db.session(principal.scope) as session:
        await repo.save_run(session, org, principal.user_id, entry.id, record)
        await repo.audit(
            session,
            org_id=org,
            actor_id=principal.user_id,
            actor_email=principal.email,
            action="agent.run",
            target_type="run",
            target_id=record.run_id,
            detail={
                "agent": entry.id,
                "site": site_data.site.slug,
                "status": record.status.value,
                "provider": record.model_provider,
            },
        )
    return record


@router.post("/agents/{agent_id}/runs")
async def run_agent(
    agent_id: str,
    body: PlatformRunRequest,
    principal: Annotated[Principal, Depends(needs(Permission.RUN_AGENTS))],
    request: Request,
) -> RunRecord:
    """Run one agent on one of the caller's sites. A run that fails its checks is still a 200
    with status 'failed', and it is stored like any other."""
    state = state_of(request)
    entry, spec, provider = await resolve_agent(request, principal, agent_id)
    if not entry.direct:
        raise HTTPException(status_code=400, detail="Ask this agent through the assistant.")
    try:
        site_data = await load_site(state.db, principal, body.site)
    except SiteNotFound:
        raise HTTPException(status_code=404, detail="No site with that name.") from None
    inputs = RunInputs(
        site=site_data.site.slug,
        question=body.question,
        audience=body.audience,
        anomaly_id=body.anomaly_id,
    )
    return await run_for_caller(state, principal, entry, spec, provider, site_data, inputs)


async def _visible_slugs(request: Request, principal: Principal) -> list[str] | None:
    """The site names a person may see runs for, or None when they may see every site."""
    if principal.site_ids is None:
        return None
    async with state_of(request).db.session(principal.scope) as session:
        sites = await repo.list_sites(session, principal.org(), principal.site_ids)
    return [s.slug for s in sites]


@router.get("/runs")
async def list_runs(
    principal: Annotated[Principal, Depends(needs(Permission.VIEW_RUNS))],
    request: Request,
    agent: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[RunSummary]:
    slugs = await _visible_slugs(request, principal)
    async with state_of(request).db.session(principal.scope) as session:
        rows = await repo.list_runs(
            session, principal.org(), agent_id=agent, only_sites=slugs, limit=limit
        )
    return [run_summary(RunRecord.model_validate(row.record)) for row in rows]


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    principal: Annotated[Principal, Depends(needs(Permission.VIEW_RUNS))],
    request: Request,
) -> dict[str, Any]:
    try:
        key = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="No run with that id.") from None
    slugs = await _visible_slugs(request, principal)
    async with state_of(request).db.session(principal.scope) as session:
        row = await repo.get_run(session, principal.org(), key, only_sites=slugs)
    if row is None:
        raise HTTPException(status_code=404, detail="No run with that id.")
    return dict(row.record)
