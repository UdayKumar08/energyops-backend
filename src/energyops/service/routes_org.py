"""What an organisation's own administrators manage: people, sites, usage and the audit log."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError

from energyops.db import repo
from energyops.db.models import User
from energyops.security.permissions import Permission
from energyops.security.principal import Principal
from energyops.service.deps import needs, state_of

router = APIRouter()
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
OrgRole = Literal["org_admin", "manager", "technician", "viewer"]
Runs = Annotated[Principal, Depends(needs(Permission.VIEW_RUNS))]
Users = Annotated[Principal, Depends(needs(Permission.MANAGE_USERS))]
Sites = Annotated[Principal, Depends(needs(Permission.MANAGE_SITES))]
Audit = Annotated[Principal, Depends(needs(Permission.VIEW_ORG_AUDIT))]


async def _audit(
    request: Request,
    principal: Principal,
    action: str,
    target_type: str,
    target: str,
    detail: dict[str, Any],
) -> None:
    async with state_of(request).db.session(principal.scope) as session:
        await repo.audit(
            session,
            org_id=principal.org(),
            actor_id=principal.user_id,
            actor_email=principal.email,
            action=action,
            target_type=target_type,
            target_id=target,
            detail=detail,
        )


# ------------------------------------------------------------------ usage and audit


class UsageLine(BaseModel):
    agent: str
    day: str
    runs: int
    failed: int
    input_tokens: int
    output_tokens: int


class UsageView(BaseModel):
    since: str
    runs: int
    failed: int
    input_tokens: int
    output_tokens: int
    lines: list[UsageLine]


@router.get("/usage")
async def usage(
    principal: Runs, request: Request, days: Annotated[int, Query(ge=1, le=366)] = 30
) -> UsageView:
    """How much this organisation's agents were used, from this organisation's own runs."""
    since = datetime.now(UTC) - timedelta(days=days)
    async with state_of(request).db.session(principal.scope) as session:
        rows = await repo.org_usage(session, principal.org(), since)
    return UsageView(
        since=since.isoformat(),
        runs=sum(r.runs for r in rows),
        failed=sum(r.failed for r in rows),
        input_tokens=sum(r.input_tokens for r in rows),
        output_tokens=sum(r.output_tokens for r in rows),
        lines=[
            UsageLine(
                agent=r.agent_id,
                day=r.day.isoformat(),
                runs=r.runs,
                failed=r.failed,
                input_tokens=r.input_tokens,
                output_tokens=r.output_tokens,
            )
            for r in rows
        ],
    )


class AuditView(BaseModel):
    at: str
    actor: str | None
    action: str
    target: str | None
    detail: dict[str, Any]


@router.get("/audit")
async def audit_log(
    principal: Audit, request: Request, limit: Annotated[int, Query(ge=1, le=500)] = 100
) -> list[AuditView]:
    async with state_of(request).db.session(principal.scope) as session:
        rows = await repo.list_audit(session, org_id=principal.org(), limit=limit)
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


# ------------------------------------------------------------------ people


class PersonView(BaseModel):
    id: str
    email: str
    name: str
    role: str
    is_active: bool
    sites: list[str] | None  # site short names, or None for every site


class NewPerson(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    name: str = Field(min_length=1, max_length=200)
    role: OrgRole
    sites: list[str] | None = Field(default=None, max_length=100)


class PersonChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: OrgRole | None = None
    is_active: bool | None = None
    sites: list[str] | None = None
    all_sites: bool | None = None


async def _site_ids(request: Request, principal: Principal, slugs: list[str]) -> list[uuid.UUID]:
    org = principal.org()
    ids = []
    async with state_of(request).db.session(principal.scope) as session:
        for slug in slugs:
            site = await repo.get_site(session, org, slug, None)
            if site is None:
                raise HTTPException(status_code=422, detail=f"There is no site called '{slug}'.")
            ids.append(site.id)
    return ids


async def _person(request: Request, principal: Principal, user: User) -> PersonView:
    org = principal.org()
    if user.all_sites:
        sites = None
    else:
        async with state_of(request).db.session(principal.scope) as session:
            allowed = await repo.site_ids_for(session, org, user.id)
            listed = await repo.list_sites(session, org, allowed)
        sites = [s.slug for s in listed]
    return PersonView(
        id=str(user.id),
        email=user.email,
        name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        sites=sites,
    )


@router.get("/users")
async def list_people(principal: Users, request: Request) -> list[PersonView]:
    async with state_of(request).db.session(principal.scope) as session:
        users = await repo.list_users(session, principal.org())
    return [await _person(request, principal, u) for u in users]


@router.post("/users", status_code=201)
async def add_person(body: NewPerson, principal: Users, request: Request) -> PersonView:
    """Add someone to this organisation. They sign in with their own account; there is no
    password here."""
    email = body.email.strip().lower()
    if not EMAIL.match(email):
        raise HTTPException(status_code=422, detail="That is not an email address.")
    site_ids = None if body.sites is None else await _site_ids(request, principal, body.sites)
    try:
        async with state_of(request).db.session(principal.scope) as session:
            user = await repo.create_user(
                session,
                org_id=principal.org(),
                email=email,
                display_name=body.name.strip(),
                role=body.role,
                site_ids=site_ids,
            )
    except IntegrityError:
        raise HTTPException(
            status_code=409, detail="That email address is already in use."
        ) from None
    await _audit(
        request, principal, "user.added", "user", str(user.id), {"email": email, "role": body.role}
    )
    return await _person(request, principal, user)


@router.patch("/users/{user_id}")
async def change_person(
    user_id: uuid.UUID, body: PersonChange, principal: Users, request: Request
) -> PersonView:
    org = principal.org()
    if body.sites is not None and body.all_sites:
        raise HTTPException(status_code=422, detail="Choose sites or all sites, not both.")
    if user_id == principal.user_id and (body.role is not None or body.is_active is False):
        raise HTTPException(
            status_code=409, detail="You cannot change your own role or switch yourself off."
        )
    site_ids = None if body.sites is None else await _site_ids(request, principal, body.sites)
    async with state_of(request).db.session(principal.scope) as session:
        user = await repo.get_user(session, user_id)
        if user is None or user.org_id != org:
            raise HTTPException(status_code=404, detail="No such person.")
        was_admin = user.role == "org_admin" and user.is_active
        if body.role is not None:
            user.role = body.role
        if body.is_active is not None:
            user.is_active = body.is_active
        if site_ids is not None:
            await repo.set_site_access(session, org, user, site_ids)
        elif body.all_sites:
            await repo.set_site_access(session, org, user, None)
        await session.flush()
        if was_admin and await repo.count_active_admins(session, org) == 0:
            raise HTTPException(
                status_code=409, detail="An organisation must keep at least one administrator."
            )
        changed = body.model_dump(exclude_none=True)
    await _audit(request, principal, "user.changed", "user", str(user_id), changed)
    async with state_of(request).db.session(principal.scope) as session:
        fresh = await repo.get_user(session, user_id)
    assert fresh is not None
    return await _person(request, principal, fresh)


# ------------------------------------------------------------------ sites


class NewSite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    interval_minutes: Literal[5, 10, 15, 30, 60] = 15


class SiteCreated(BaseModel):
    slug: str
    name: str


@router.post("/sites", status_code=201)
async def add_site(body: NewSite, principal: Sites, request: Request) -> SiteCreated:
    if not SLUG.match(body.slug):
        raise HTTPException(status_code=422, detail="The short name must look like north-wing.")
    try:
        async with state_of(request).db.session(principal.scope) as session:
            site, _ = await repo.create_site(
                session,
                principal.org(),
                body.slug,
                body.name.strip(),
                interval_minutes=body.interval_minutes,
            )
    except IntegrityError:
        raise HTTPException(status_code=409, detail="A site with that short name exists.") from None
    await _audit(request, principal, "site.created", "site", site.slug, {"name": site.name})
    return SiteCreated(slug=site.slug, name=site.name)
