"""Sign-in, the signed-in person, sites, readings, tariffs and events."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from energyops.anomalies import detect_anomalies
from energyops.calculations import EnergyDataError
from energyops.db import repo
from energyops.facts import build_fact_sheet
from energyops.mcp_servers.schema import AnomalyListModel, FactSheetModel
from energyops.security.auth import NotProvisioned, authenticate
from energyops.security.permissions import Permission
from energyops.security.principal import Principal
from energyops.security.tokens import Claims
from energyops.service.data import SiteNotFound, load_site
from energyops.service.deps import current_principal, needs, state_of
from energyops.tariffs import CURRENCY_SYMBOLS, Tariff, TariffBand, TariffError

router = APIRouter()
Caller = Annotated[Principal, Depends(current_principal)]
Viewer = Annotated[Principal, Depends(needs(Permission.VIEW_DATA))]


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class SiteView(BaseModel):
    slug: str
    name: str


class MeView(BaseModel):
    user_id: str
    email: str
    name: str
    role: str
    organisation: str | None
    permissions: list[str]
    sites: list[SiteView]


@router.post("/auth/dev-login")
async def dev_login(body: LoginRequest, request: Request) -> LoginResponse:
    """Local development only: sign in as a seeded person by email, with no password. The route
    answers 404 anywhere that has no development secret, which is everywhere but local."""
    state = state_of(request)
    if state.local_tokens is None:
        raise HTTPException(status_code=404, detail="Not found.")
    email = body.email.strip().lower()
    try:
        await authenticate(
            state.db, Claims(subject=email, email=email, name=None, external_id=None)
        )
    except NotProvisioned as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    minutes = 60
    return LoginResponse(
        access_token=state.local_tokens.issue(email, minutes=minutes), expires_in=minutes * 60
    )


@router.get("/me")
async def me(principal: Caller, request: Request) -> MeView:
    state = state_of(request)
    sites: list[SiteView] = []
    organisation: str | None = None
    if principal.org_id is not None:
        async with state.db.session(principal.scope) as session:
            rows = await repo.list_sites(session, principal.org_id, principal.site_ids)
            sites = [SiteView(slug=s.slug, name=s.name) for s in rows]
        async with state.db.session(principal.scope) as session:
            org = await repo.get_organization(session, principal.org_id)
            organisation = org.name if org else None
    return MeView(
        user_id=str(principal.user_id),
        email=principal.email,
        name=principal.name,
        role=principal.role,
        organisation=organisation,
        permissions=sorted(p.value for p in principal.permissions),
        sites=sites,
    )


# ------------------------------------------------------------------ sites


class SiteListItem(BaseModel):
    slug: str
    name: str
    first_reading: str | None
    last_reading: str | None
    has_tariff: bool


@router.get("/sites")
async def list_sites(principal: Viewer, request: Request) -> list[SiteListItem]:
    state = state_of(request)
    org = principal.org()
    items: list[SiteListItem] = []
    async with state.db.session(principal.scope) as session:
        for site in await repo.list_sites(session, org, principal.site_ids):
            meter = await repo.primary_meter(session, org, site.id)
            span = await repo.reading_range(session, org, meter.id) if meter else None
            tariff = await repo.get_tariff(session, org, site.id)
            items.append(
                SiteListItem(
                    slug=site.slug,
                    name=site.name,
                    first_reading=span[0].isoformat() if span else None,
                    last_reading=span[1].isoformat() if span else None,
                    has_tariff=tariff is not None,
                )
            )
    return items


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="No site with that name.")


@router.get("/sites/{slug}/summary")
async def site_summary(
    slug: str,
    principal: Viewer,
    request: Request,
) -> FactSheetModel:
    """The computed figures for a site, the same ones the agents are given."""
    try:
        data = await load_site(state_of(request).db, principal, slug)
    except SiteNotFound:
        raise _not_found() from None
    try:
        return FactSheetModel.from_fact_sheet(build_fact_sheet(data.readings))
    except EnergyDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


class ReadingPoint(BaseModel):
    ts: str
    kw: float


@router.get("/sites/{slug}/series")
async def site_series(
    slug: str,
    principal: Viewer,
    request: Request,
    days: Annotated[int, Query(ge=1, le=28)] = 7,
) -> list[ReadingPoint]:
    """Hourly average demand for the last `days` days, for charts."""
    try:
        data = await load_site(state_of(request).db, principal, slug, days=days)
    except SiteNotFound:
        raise _not_found() from None
    hours: dict[str, list[float]] = {}
    for reading in data.readings:
        key = reading.timestamp.replace(minute=0, second=0, microsecond=0).isoformat()
        hours.setdefault(key, []).append(reading.power_kw)
    return [ReadingPoint(ts=ts, kw=round(sum(v) / len(v), 3)) for ts, v in hours.items()]


@router.get("/sites/{slug}/anomalies")
async def site_anomalies(
    slug: str,
    principal: Viewer,
    request: Request,
) -> AnomalyListModel:
    try:
        data = await load_site(state_of(request).db, principal, slug)
    except SiteNotFound:
        raise _not_found() from None
    try:
        return AnomalyListModel.from_result(detect_anomalies(data.readings))
    except EnergyDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


# ------------------------------------------------------------------ tariffs


class BandBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=40)
    start_hour: int = Field(ge=0, le=23)
    end_hour: int = Field(ge=1, le=24)
    price_per_kwh: float = Field(ge=0)
    days: Literal["all", "weekday", "weekend"] = "all"


class TariffBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=200)
    currency: Literal["GBP", "EUR", "USD"]
    price_per_kwh: float = Field(ge=0)
    bands: list[BandBody] = Field(default_factory=list, max_length=12)
    demand_charge_per_kw_month: float = Field(default=0.0, ge=0)


class TariffView(TariffBody):
    symbol: str
    is_demo: bool


def _tariff_view(tariff: Tariff) -> TariffView:
    return TariffView(
        label=tariff.label,
        currency=tariff.currency,
        price_per_kwh=tariff.price_per_kwh,
        bands=[
            BandBody(
                name=b.name,
                start_hour=b.start_hour,
                end_hour=b.end_hour,
                price_per_kwh=b.price_per_kwh,
                days=b.days,
            )
            for b in tariff.bands
        ],
        demand_charge_per_kw_month=tariff.demand_charge_per_kw_month,
        symbol=CURRENCY_SYMBOLS[tariff.currency],
        is_demo=tariff.is_demo,
    )


@router.get("/sites/{slug}/tariff")
async def get_tariff(
    slug: str,
    principal: Viewer,
    request: Request,
) -> TariffView:
    state = state_of(request)
    org = principal.org()
    async with state.db.session(principal.scope) as session:
        site = await repo.get_site(session, org, slug, principal.site_ids)
        if site is None:
            raise _not_found()
        tariff = await repo.get_tariff(session, org, site.id)
    if tariff is None:
        raise HTTPException(status_code=404, detail="No tariff is set for this site.")
    return _tariff_view(tariff)


@router.put("/sites/{slug}/tariff")
async def put_tariff(
    slug: str,
    body: TariffBody,
    principal: Annotated[Principal, Depends(needs(Permission.MANAGE_TARIFFS))],
    request: Request,
) -> TariffView:
    state = state_of(request)
    org = principal.org()
    try:
        tariff = Tariff(
            label=body.label,
            currency=body.currency,
            price_per_kwh=body.price_per_kwh,
            bands=tuple(
                TariffBand(b.name, b.start_hour, b.end_hour, b.price_per_kwh, b.days)
                for b in body.bands
            ),
            demand_charge_per_kw_month=body.demand_charge_per_kw_month,
            is_demo=False,
        )
    except TariffError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    async with state.db.session(principal.scope) as session:
        site = await repo.get_site(session, org, slug, principal.site_ids)
        if site is None:
            raise _not_found()
        await repo.set_tariff(session, org, site.id, tariff)
        await repo.audit(
            session,
            org_id=org,
            actor_id=principal.user_id,
            actor_email=principal.email,
            action="tariff.set",
            target_type="site",
            target_id=site.slug,
            detail={"label": tariff.label, "currency": tariff.currency},
        )
    return _tariff_view(tariff)
