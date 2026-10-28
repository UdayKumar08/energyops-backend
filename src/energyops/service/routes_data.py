"""Uploading meter readings, and the demand forecast."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from energyops.calculations import EnergyDataError
from energyops.db import repo
from energyops.forecast import Forecast, ForecastRefused, forecast_demand
from energyops.ingest import MAX_BYTES, gap_count, parse_csv
from energyops.security.permissions import Permission
from energyops.security.principal import Principal
from energyops.service.data import SiteNotFound, load_site
from energyops.service.deps import needs, state_of
from energyops.service.state import ServiceState
from energyops.service.uploads import read_capped
from energyops.synthetic import MeterReading

router = APIRouter()
Uploader = Annotated[Principal, Depends(needs(Permission.UPLOAD_DATA))]
Viewer = Annotated[Principal, Depends(needs(Permission.VIEW_DATA))]

FORECAST_HISTORY_DAYS = 56


class IssueView(BaseModel):
    line: int
    message: str


class UploadResult(BaseModel):
    accepted: bool
    dry_run: bool
    rows: int
    first: datetime | None
    last: datetime | None
    missing_intervals: int
    # Readings already stored in the same time range. Rows with a matching timestamp are replaced.
    existing_in_range: int
    issue_count: int
    issues: list[IssueView]
    ingestion_id: str | None = None


@router.post("/sites/{slug}/readings/upload", response_model=UploadResult)
async def upload_readings(
    slug: str,
    principal: Uploader,
    request: Request,
    dry_run: Annotated[bool, Query()] = False,
    filename: Annotated[str | None, Query(max_length=200)] = None,
) -> Any:
    """Send a CSV file (`timestamp,power_kw`) as the request body. The file is accepted whole or
    refused whole. With `dry_run=true` it is checked and nothing is stored."""
    state = state_of(request)
    org = principal.org()
    data = await read_capped(request, MAX_BYTES + 1024)
    async with state.db.session(principal.scope) as session:
        site = await repo.get_site(session, org, slug, principal.site_ids)
        if site is None:
            raise HTTPException(status_code=404, detail="No site with that name.")
        meter = await repo.primary_meter(session, org, site.id)
        if meter is None:
            raise HTTPException(
                status_code=409, detail="This site has no meter to store readings for."
            )

        report = await asyncio.to_thread(parse_csv, data, interval_minutes=meter.interval_minutes)
        result = UploadResult(
            accepted=False,
            dry_run=dry_run,
            rows=report.rows,
            first=report.first,
            last=report.last,
            missing_intervals=gap_count(report, meter.interval_minutes) if report.ok else 0,
            existing_in_range=0,
            issue_count=report.issue_count,
            issues=[IssueView(line=i.line, message=i.message) for i in report.issues],
        )
        if not report.ok:
            return JSONResponse(status_code=422, content=result.model_dump(mode="json"))
        assert report.first is not None and report.last is not None
        result.existing_in_range = await repo.count_readings_between(
            session, org, meter.id, report.first, report.last
        )
        if dry_run:
            return result

        stored = [
            MeterReading(site.slug, r.timestamp, meter.interval_minutes, r.power_kw)
            for r in report.readings
        ]
        await repo.upsert_readings(session, org, meter.id, stored)
        ingestion = await repo.record_ingestion(
            session,
            org_id=org,
            site_id=site.id,
            meter_id=meter.id,
            uploaded_by=principal.user_id,
            filename=filename,
            sha256=hashlib.sha256(data).hexdigest(),
            byte_size=len(data),
            row_count=len(stored),
            first_ts=report.first,
            last_ts=report.last,
        )
        await repo.audit(
            session,
            org_id=org,
            actor_id=principal.user_id,
            actor_email=principal.email,
            action="readings.upload",
            target_type="site",
            target_id=site.slug,
            detail={
                "rows": len(stored),
                "first": report.first.isoformat(),
                "last": report.last.isoformat(),
                "replaced_range_rows": result.existing_in_range,
                "sha256": ingestion.sha256,
            },
        )
    result.accepted = True
    result.ingestion_id = str(ingestion.id)
    return result


class IngestionView(BaseModel):
    id: str
    site: str
    filename: str | None
    rows: int
    first: datetime
    last: datetime
    uploaded_at: datetime


@router.get("/ingestions")
async def list_ingestions(principal: Viewer, request: Request) -> list[IngestionView]:
    state = state_of(request)
    async with state.db.session(principal.scope) as session:
        found = await repo.list_ingestions(session, principal.org(), principal.site_ids)
    return [
        IngestionView(
            id=str(i.id),
            site=s.slug,
            filename=i.filename,
            rows=i.row_count,
            first=i.first_ts,
            last=i.last_ts,
            uploaded_at=i.created_at,
        )
        for i, s in found
    ]


# ------------------------------------------------------------------ forecast


class ForecastDayView(BaseModel):
    day: date
    energy_kwh: float
    peak_kw: float


class ForecastPointView(BaseModel):
    timestamp: datetime
    power_kw: float


class BacktestView(BaseModel):
    days_tested: int
    mae_kw: float
    mae_percent_of_mean: float | None
    daily_energy_error_percent: float | None
    naive_mae_kw: float | None
    beats_naive: bool | None


class ForecastView(BaseModel):
    site: str
    method: str
    history_days: int
    interval_minutes: int
    days: list[ForecastDayView]
    points: list[ForecastPointView]
    backtest: BacktestView | None
    backtest_note: str | None
    limits: list[str]


def _finite(value: float) -> float | None:
    return value if value == value and abs(value) != float("inf") else None


def forecast_view(site: str, result: Forecast) -> ForecastView:
    backtest = result.backtest
    return ForecastView(
        site=site,
        method=result.method,
        history_days=result.history_days,
        interval_minutes=result.interval_minutes,
        days=[
            ForecastDayView(day=d.day, energy_kwh=d.energy_kwh, peak_kw=d.peak_kw)
            for d in result.days
        ],
        points=[
            ForecastPointView(timestamp=p.timestamp, power_kw=p.power_kw) for p in result.points
        ],
        backtest=(
            None
            if backtest is None
            else BacktestView(
                days_tested=backtest.days_tested,
                mae_kw=backtest.mae_kw,
                mae_percent_of_mean=_finite(backtest.mae_percent_of_mean),
                daily_energy_error_percent=_finite(backtest.daily_energy_error_percent),
                naive_mae_kw=backtest.naive_mae_kw,
                beats_naive=backtest.beats_naive,
            )
        ),
        backtest_note=result.backtest_note,
        limits=list(result.limits),
    )


async def forecast_for(
    state: ServiceState, principal: Principal, slug: str | None, days: int
) -> ForecastView:
    """The forecast for one of the caller's own sites. Raises HTTPException for the cases a
    caller can fix: an unknown site (404) and too little or unusable history (422)."""
    try:
        data = await load_site(state.db, principal, slug, days=FORECAST_HISTORY_DAYS)
    except SiteNotFound:
        raise HTTPException(status_code=404, detail="No site with that name.") from None
    try:
        result = await asyncio.to_thread(forecast_demand, data.readings, days=days)
    except (ForecastRefused, EnergyDataError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return forecast_view(data.site.slug, result)


@router.get("/sites/{slug}/forecast")
async def site_forecast(
    slug: str,
    principal: Viewer,
    request: Request,
    days: Annotated[int, Query(ge=1, le=14)] = 7,
) -> ForecastView:
    return await forecast_for(state_of(request), principal, slug, days)
