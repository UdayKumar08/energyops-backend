"""CSV upload and the forecast, through the API on a real PostgreSQL."""

from __future__ import annotations

import httpx

from service_helpers import ADMIN, MANAGER, NORTH, PLATFORM, TECH, VIEWER, as_user

NORTH_MANAGER = "manager@northside.example"
FOUR = (
    "timestamp,power_kw\n"
    "2026-04-01T00:00:00Z,10\n2026-04-01T00:15:00Z,20\n"
    "2026-04-01T00:30:00Z,30\n2026-04-01T00:45:00Z,40\n"
)


async def upload(
    api: httpx.AsyncClient, user: str, site: str, body: str | bytes = FOUR, **params: object
) -> httpx.Response:
    return await api.post(
        f"/sites/{site}/readings/upload",
        content=body,
        params=params or None,
        headers={**as_user(user), "Content-Type": "text/csv"},
    )


async def last_hour(api: httpx.AsyncClient, user: str, site: str) -> list[dict[str, object]]:
    response = await api.get(f"/sites/{site}/series", params={"days": 1}, headers=as_user(user))
    assert response.status_code == 200
    return response.json()  # type: ignore[no-any-return]


# ------------------------------------------------------------------ upload


async def test_a_manager_uploads_readings_and_they_appear(api: httpx.AsyncClient) -> None:
    response = await upload(api, MANAGER, "demo-office", filename="april.csv")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] and body["rows"] == 4 and body["missing_intervals"] == 0
    assert body["ingestion_id"] and body["issue_count"] == 0
    assert await last_hour(api, MANAGER, "demo-office") == [
        {"ts": "2026-04-01T00:00:00+00:00", "kw": 25.0}
    ]
    listed = (await api.get("/ingestions", headers=as_user(MANAGER))).json()
    assert listed[0]["site"] == "demo-office" and listed[0]["rows"] == 4
    assert listed[0]["filename"] == "april.csv"
    audit = (await api.get("/audit", headers=as_user(ADMIN))).json()
    entry = next(a for a in audit if a["action"] == "readings.upload")
    assert entry["target"] == "demo-office" and entry["detail"]["rows"] == 4
    assert len(entry["detail"]["sha256"]) == 64


async def test_a_dry_run_checks_the_file_and_stores_nothing(api: httpx.AsyncClient) -> None:
    before = await last_hour(api, MANAGER, "demo-office")
    response = await upload(api, MANAGER, "demo-office", dry_run="true")
    assert response.status_code == 200
    assert response.json()["dry_run"] and not response.json()["accepted"]
    assert response.json()["ingestion_id"] is None
    assert await last_hour(api, MANAGER, "demo-office") == before
    assert (await api.get("/ingestions", headers=as_user(MANAGER))).json() == []


async def test_a_bad_file_is_refused_whole_with_line_numbers(api: httpx.AsyncClient) -> None:
    before = await last_hour(api, MANAGER, "demo-office")
    bad = FOUR + "2026-04-01T01:00:00,5\n2026-04-01T01:15:00Z,-3\n"
    response = await upload(api, MANAGER, "demo-office", bad)
    assert response.status_code == 422
    body = response.json()
    assert not body["accepted"] and body["issue_count"] == 2
    assert [i["line"] for i in body["issues"]] == [6, 7]
    assert await last_hour(api, MANAGER, "demo-office") == before
    assert (await api.get("/ingestions", headers=as_user(MANAGER))).json() == []


async def test_uploading_again_replaces_the_readings_with_the_same_time(
    api: httpx.AsyncClient,
) -> None:
    await upload(api, MANAGER, "demo-office")
    second = await upload(api, MANAGER, "demo-office", FOUR.replace(",40", ",80"))
    assert second.json()["existing_in_range"] == 4
    assert await last_hour(api, MANAGER, "demo-office") == [
        {"ts": "2026-04-01T00:00:00+00:00", "kw": 35.0}
    ]


async def test_gaps_in_a_file_are_reported_but_allowed(api: httpx.AsyncClient) -> None:
    body = "timestamp,power_kw\n2026-04-01T00:00:00Z,1\n2026-04-01T01:00:00Z,1\n"
    response = await upload(api, MANAGER, "demo-office", body)
    assert response.status_code == 200 and response.json()["missing_intervals"] == 3


async def test_only_roles_that_may_upload_can(api: httpx.AsyncClient) -> None:
    for user in (VIEWER, TECH, PLATFORM):
        assert (await upload(api, user, "demo-hotel")).status_code == 403
    assert (await upload(api, ADMIN, "demo-office")).status_code == 200


async def test_another_organisations_site_looks_like_it_does_not_exist(
    api: httpx.AsyncClient,
) -> None:
    other = await upload(api, NORTH_MANAGER, "demo-office")
    missing = await upload(api, NORTH_MANAGER, "no-such-site")
    assert other.status_code == missing.status_code == 404
    assert other.json() == missing.json()
    assert (await api.get("/ingestions", headers=as_user(MANAGER))).json() == []


async def test_a_manager_limited_to_one_site_cannot_upload_to_another(
    api: httpx.AsyncClient,
) -> None:
    made = await api.post(
        "/users",
        json={
            "email": "limited@harbour.example",
            "name": "Lee",
            "role": "manager",
            "sites": ["demo-hotel"],
        },
        headers=as_user(ADMIN),
    )
    assert made.status_code == 201, made.text
    assert (await upload(api, "limited@harbour.example", "demo-hotel")).status_code == 200
    assert (await upload(api, "limited@harbour.example", "demo-office")).status_code == 404
    seen = (await api.get("/ingestions", headers=as_user("limited@harbour.example"))).json()
    assert {i["site"] for i in seen} == {"demo-hotel"}
    # An organisation-wide manager sees the upload too, and no one else does.
    assert len((await api.get("/ingestions", headers=as_user(MANAGER))).json()) == 1
    assert (await api.get("/ingestions", headers=as_user(NORTH_MANAGER))).json() == []


async def test_an_oversized_upload_is_refused_before_it_is_read(api: httpx.AsyncClient) -> None:
    response = await upload(api, MANAGER, "demo-office", b"x" * (5 * 1024 * 1024 + 2048))
    assert response.status_code == 413


async def test_no_token_no_upload(api: httpx.AsyncClient) -> None:
    response = await api.post("/sites/demo-office/readings/upload", content=FOUR)
    assert response.status_code == 401


# ------------------------------------------------------------------ forecast


async def test_a_forecast_comes_with_its_test_and_its_limits(api: httpx.AsyncClient) -> None:
    response = await api.get("/sites/demo-hotel/forecast", headers=as_user(VIEWER))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["site"] == "demo-hotel" and len(body["days"]) == 7
    assert len(body["points"]) == 7 * 96 and body["history_days"] == 28
    assert body["backtest"]["days_tested"] == 7
    assert body["backtest"]["beats_naive"] in (True, False)
    assert any("weather" in text for text in body["limits"])
    assert body["days"][0]["day"] == "2026-03-30"


async def test_the_horizon_is_limited(api: httpx.AsyncClient) -> None:
    for days in (0, 15):
        response = await api.get(
            "/sites/demo-hotel/forecast", params={"days": days}, headers=as_user(MANAGER)
        )
        assert response.status_code == 422


async def test_a_site_with_no_history_is_refused_with_a_reason(api: httpx.AsyncClient) -> None:
    await api.post("/sites", json={"slug": "new-site", "name": "New site"}, headers=as_user(ADMIN))
    response = await api.get("/sites/new-site/forecast", headers=as_user(ADMIN))
    assert response.status_code == 422
    assert "readings" in response.json()["detail"]


async def test_a_forecast_cannot_cross_an_organisation_or_site_limit(
    api: httpx.AsyncClient,
) -> None:
    assert (await api.get("/sites/demo-hotel/forecast", headers=as_user(NORTH))).status_code == 404
    assert (await api.get("/sites/demo-office/forecast", headers=as_user(TECH))).status_code == 404
    assert (
        await api.get("/sites/demo-hotel/forecast", headers=as_user(PLATFORM))
    ).status_code == 403


async def test_uploaded_history_feeds_the_forecast(api: httpx.AsyncClient) -> None:
    """Two weeks of flat readings uploaded to an empty site give a flat forecast."""
    await api.post("/sites", json={"slug": "flat", "name": "Flat"}, headers=as_user(ADMIN))
    rows = ["timestamp,power_kw"]
    for day in range(14):
        for slot in range(96):
            minutes = day * 1440 + slot * 15
            clock = f"{minutes % 1440 // 60:02d}:{minutes % 60:02d}"
            stamp = f"2026-03-{1 + minutes // 1440:02d}T{clock}:00Z"
            rows.append(f"{stamp},12")
    uploaded = await upload(api, ADMIN, "flat", "\n".join(rows) + "\n")
    assert uploaded.status_code == 200, uploaded.text
    body = (
        await api.get("/sites/flat/forecast", params={"days": 2}, headers=as_user(ADMIN))
    ).json()
    assert body["days"][0]["peak_kw"] == 12.0
    assert round(body["days"][0]["energy_kwh"], 6) == 12 * 24
    assert body["backtest"] is None and body["backtest_note"]
