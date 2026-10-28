"""Platform admin routes: the agent registry and its settings, organisations, usage and the
platform audit log. A platform admin runs the product. They hold none of the customer
permissions, so nothing here returns a customer's readings, runs, tariffs or documents."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError

from energyops.agents.registry import AGENTS, AgentEntry, DataSources, InvalidRunInput, RunInputs
from energyops.agents.runs import RunRecord
from energyops.agents.sources import demo_tariff_source, synthetic_demo_source
from energyops.agents.spec import AgentSpec, load_prompt, unimplemented_guardrails
from energyops.api.providers import ProviderId, ProviderInfo, ProviderUnavailable, provider_infos
from energyops.api.views import AgentDetail, AgentSummary, PromptView, summarise
from energyops.db import repo
from energyops.mcp_servers.energy import create_server
from energyops.security.permissions import Permission
from energyops.security.principal import Principal
from energyops.service.agent_config import (
    AgentOverrides,
    InvalidOverrides,
    effective_spec,
    parse_overrides,
)
from energyops.service.deps import needs, state_of
from energyops.service.routes_agents import entry_for

router = APIRouter(prefix="/admin")
Admin = Annotated[Principal, Depends(needs(Permission.ADMIN_PLATFORM))]
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


async def _overrides(request: Request, principal: Principal) -> dict[str, dict[str, Any]]:
    async with state_of(request).db.session(principal.scope) as session:
        return await repo.get_agent_overrides(session)


async def _audit(
    request: Request, principal: Principal, action: str, target: str, detail: dict[str, Any]
) -> None:
    async with state_of(request).db.session(principal.scope) as session:
        await repo.audit(
            session,
            org_id=None,
            actor_id=principal.user_id,
            actor_email=principal.email,
            action=action,
            target_type="platform",
            target_id=target,
            detail=detail,
        )


# ------------------------------------------------------------------ agents


class AdminAgentRow(BaseModel):
    summary: AgentSummary
    built_in_status: str
    settings: dict[str, Any]


class AdminAgentDetail(AgentDetail):
    built_in_spec: dict[str, Any]
    settings: dict[str, Any]


def _row(entry: AgentEntry, stored: dict[str, Any] | None) -> AdminAgentRow:
    try:
        spec = effective_spec(entry.spec, stored)
    except InvalidOverrides:  # stored settings that no longer validate: show the built-in spec
        spec = entry.spec
    return AdminAgentRow(
        summary=summarise(entry, spec),
        built_in_status=entry.spec.status.value,
        settings=stored or {},
    )


def _detail(entry: AgentEntry, spec: AgentSpec, stored: dict[str, Any]) -> AdminAgentDetail:
    prompt = load_prompt(spec)
    return AdminAgentDetail(
        summary=summarise(entry, spec),
        spec=spec.model_dump(mode="json"),
        prompt=PromptView(version=prompt.version, sha256=prompt.sha256, text=prompt.text),
        guardrails_not_implemented=unimplemented_guardrails(spec),
        built_in_spec=entry.spec.model_dump(mode="json"),
        settings=stored,
    )


@router.get("/providers")
async def providers(principal: Admin, request: Request) -> list[ProviderInfo]:
    return provider_infos(state_of(request).keys())


@router.get("/agents")
async def list_agents(principal: Admin, request: Request) -> list[AdminAgentRow]:
    stored = await _overrides(request, principal)
    return [_row(entry, stored.get(entry.id)) for entry in AGENTS.values()]


@router.get("/agents/{agent_id}")
async def agent_detail(agent_id: str, principal: Admin, request: Request) -> AdminAgentDetail:
    entry = entry_for(agent_id)
    stored = (await _overrides(request, principal)).get(agent_id, {})
    try:
        spec = effective_spec(entry.spec, stored)
    except InvalidOverrides:
        spec = entry.spec
    return _detail(entry, spec, stored)


@router.put("/agents/{agent_id}/settings")
async def put_agent_settings(
    agent_id: str, body: AgentOverrides, principal: Admin, request: Request
) -> AdminAgentDetail:
    """Replace the agent's operating settings. The result is validated as a whole spec first."""
    entry = entry_for(agent_id)
    stored = body.stored()
    try:
        spec = effective_spec(entry.spec, stored)
    except InvalidOverrides as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    async with state_of(request).db.session(principal.scope) as session:
        previous = (await repo.get_agent_overrides(session)).get(agent_id, {})
        await repo.set_agent_overrides(session, agent_id, stored, principal.user_id)
    await _audit(
        request, principal, "agent.settings_changed", agent_id, {"from": previous, "to": stored}
    )
    return _detail(entry, spec, stored)


@router.delete("/agents/{agent_id}/settings")
async def reset_agent_settings(
    agent_id: str, principal: Admin, request: Request
) -> AdminAgentDetail:
    """Go back to the built-in spec. An agent with no stored status is a draft, so this switches
    the agent off for customers."""
    entry = entry_for(agent_id)
    async with state_of(request).db.session(principal.scope) as session:
        previous = (await repo.get_agent_overrides(session)).get(agent_id, {})
        await repo.set_agent_overrides(session, agent_id, {}, principal.user_id)
    await _audit(request, principal, "agent.settings_reset", agent_id, {"from": previous})
    return _detail(entry, entry.spec, {})


class PlaygroundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str | None = Field(default=None, min_length=1, max_length=500)
    audience: str | None = Field(default=None, max_length=32)
    anomaly_id: str | None = Field(default=None, max_length=8)
    site: str | None = Field(default=None, max_length=64)
    provider: ProviderId = "test"
    model: str | None = Field(default=None, min_length=1, max_length=100)
    data_access: Literal["mcp", "direct"] = "mcp"


@router.post("/agents/{agent_id}/playground")
async def playground(
    agent_id: str, body: PlaygroundRequest, principal: Admin, request: Request
) -> RunRecord:
    """Try an agent on the built-in invented demo sites, before switching it on. Uses the agent's
    current settings but the provider chosen here. Not stored with any customer's runs, because
    it touches no customer's data."""
    state = state_of(request)
    entry = entry_for(agent_id)
    stored = (await _overrides(request, principal)).get(agent_id, {})
    try:
        spec = effective_spec(entry.spec, stored)
    except InvalidOverrides as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    readings = synthetic_demo_source()
    tariffs = demo_tariff_source()
    data = DataSources(readings, tariffs, create_server(source=readings, tariffs=tariffs))
    try:
        run_request = entry.make_request(
            RunInputs(
                site=body.site,
                question=body.question,
                audience=body.audience,
                anomaly_id=body.anomaly_id,
            )
        )
    except InvalidRunInput as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    try:
        test_reply = entry.test_reply(data, run_request)
    except (LookupError, ValueError):
        test_reply = "{}"
    try:
        client = state.client_factory(
            body.provider, body.model or spec.config.model.name, state.keys(), test_reply
        )
    except ProviderUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    record = await entry.build(spec, client, entry.make_facts(spec, body.data_access, data)).run(
        run_request
    )
    await _audit(
        request,
        principal,
        "agent.playground_run",
        agent_id,
        {"provider": record.model_provider, "status": record.status.value},
    )
    return record


# ------------------------------------------------------------------ organisations


class OrganisationView(BaseModel):
    id: str
    name: str
    slug: str
    is_active: bool
    created_at: str
    people: int


class PersonView(BaseModel):
    id: str
    email: str
    name: str
    role: str
    is_active: bool


class NewOrganisation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=200)
    slug: str = Field(min_length=2, max_length=64)
    admin_email: str = Field(min_length=3, max_length=320)
    admin_name: str = Field(min_length=1, max_length=200)


class OrganisationChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_active: bool


@router.get("/organizations")
async def organisations(principal: Admin, request: Request) -> list[OrganisationView]:
    async with state_of(request).db.session(principal.scope) as session:
        orgs = await repo.list_organizations(session)
        views = []
        for org in orgs:
            people = await repo.list_users(session, org.id)
            views.append(
                OrganisationView(
                    id=str(org.id),
                    name=org.name,
                    slug=org.slug,
                    is_active=org.is_active,
                    created_at=org.created_at.isoformat(),
                    people=len(people),
                )
            )
    return views


@router.post("/organizations", status_code=201)
async def create_organisation(
    body: NewOrganisation, principal: Admin, request: Request
) -> OrganisationView:
    """Create a customer organisation and its first administrator. The administrator signs in
    and adds everyone else; the platform admin never gains access to the customer's data."""
    if not SLUG.match(body.slug):
        raise HTTPException(status_code=422, detail="The short name must look like acme-energy.")
    email = body.admin_email.strip().lower()
    if not EMAIL.match(email):
        raise HTTPException(status_code=422, detail="That is not an email address.")
    try:
        async with state_of(request).db.session(principal.scope) as session:
            org = await repo.create_organization(session, body.name.strip(), body.slug)
            await repo.create_user(
                session,
                org_id=org.id,
                email=email,
                display_name=body.admin_name.strip(),
                role="org_admin",
            )
            org_id, created = org.id, org.created_at
    except IntegrityError:
        raise HTTPException(
            status_code=409, detail="That short name or email address is already in use."
        ) from None
    await _audit(request, principal, "organization.created", str(org_id), {"slug": body.slug})
    return OrganisationView(
        id=str(org_id),
        name=body.name.strip(),
        slug=body.slug,
        is_active=True,
        created_at=created.isoformat(),
        people=1,
    )


@router.patch("/organizations/{org_id}")
async def change_organisation(
    org_id: uuid.UUID, body: OrganisationChange, principal: Admin, request: Request
) -> OrganisationView:
    async with state_of(request).db.session(principal.scope) as session:
        org = await repo.get_organization(session, org_id)
        if org is None:
            raise HTTPException(status_code=404, detail="No such organisation.")
        org.is_active = body.is_active
        people = len(await repo.list_users(session, org.id))
        view = OrganisationView(
            id=str(org.id),
            name=org.name,
            slug=org.slug,
            is_active=org.is_active,
            created_at=org.created_at.isoformat(),
            people=people,
        )
    action = "organization.activated" if body.is_active else "organization.deactivated"
    await _audit(request, principal, action, str(org_id), {})
    return view


@router.get("/organizations/{org_id}/users")
async def organisation_users(
    org_id: uuid.UUID, principal: Admin, request: Request
) -> list[PersonView]:
    async with state_of(request).db.session(principal.scope) as session:
        if await repo.get_organization(session, org_id) is None:
            raise HTTPException(status_code=404, detail="No such organisation.")
        users = await repo.list_users(session, org_id)
    return [
        PersonView(
            id=str(u.id), email=u.email, name=u.display_name, role=u.role, is_active=u.is_active
        )
        for u in users
    ]


# ------------------------------------------------------------------ usage and audit


class UsageLine(BaseModel):
    organisation: str
    agent: str
    day: str
    runs: int
    failed: int
    input_tokens: int
    output_tokens: int


class UsageReport(BaseModel):
    since: str
    runs: int
    failed: int
    input_tokens: int
    output_tokens: int
    lines: list[UsageLine]
    note: str


@router.get("/usage")
async def usage(
    principal: Admin, request: Request, days: Annotated[int, Query(ge=1, le=366)] = 30
) -> UsageReport:
    """Counts and token totals per organisation, agent and day. Counts only: the content of any
    run stays with the customer."""
    since = datetime.now(UTC) - timedelta(days=days)
    async with state_of(request).db.session(principal.scope) as session:
        rows = await repo.platform_usage(session, since)
        names = {o.id: o.name for o in await repo.list_organizations(session)}
    lines = [
        UsageLine(
            organisation=names.get(r.org_id, "(removed)"),
            agent=r.agent_id,
            day=r.day.isoformat(),
            runs=r.runs,
            failed=r.failed,
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
        )
        for r in rows
    ]
    return UsageReport(
        since=since.isoformat(),
        runs=sum(r.runs for r in rows),
        failed=sum(r.failed for r in rows),
        input_tokens=sum(r.input_tokens for r in rows),
        output_tokens=sum(r.output_tokens for r in rows),
        lines=lines,
        note="Token counts are those the provider reported, or an estimate where it did not. "
        "They are not a bill.",
    )


class AuditView(BaseModel):
    at: str
    actor: str | None
    action: str
    target: str | None
    detail: dict[str, Any]


@router.get("/audit")
async def audit(
    principal: Admin, request: Request, limit: Annotated[int, Query(ge=1, le=500)] = 100
) -> list[AuditView]:
    async with state_of(request).db.session(principal.scope) as session:
        rows = await repo.list_audit(session, org_id=None, limit=limit)
    return [
        AuditView(
            at=r.created_at.isoformat(),
            actor=r.actor_email,
            action=r.action,
            target=r.target_id,
            detail=r.detail,
        )
        for r in rows
    ]


__all__ = ["router", "parse_overrides"]
