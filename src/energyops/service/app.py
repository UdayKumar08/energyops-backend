"""The authenticated platform API.

Every route except /health and the local-only dev sign-in needs a valid bearer token, and what a
person may do and see is decided from our own database and the permission map, never from the
token body. The browser does not call this directly: the web application's server holds the
session and forwards calls (see docs/build-plan.md).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware

from energyops.api.providers import make_client
from energyops.config import Environment, Settings, get_settings
from energyops.db.checkpoint import Checkpointing
from energyops.db.session import Database
from energyops.models.credentials import ProviderKeys
from energyops.security.permissions import Forbidden
from energyops.security.tokens import TokenVerifier
from energyops.security.verifier import local_tokens_from, verifier_from
from energyops.service import (
    routes_admin,
    routes_agents,
    routes_assistant,
    routes_basic,
    routes_org,
    routes_tickets,
)
from energyops.service.state import ClientFactory, ServiceState

LOCAL_HOSTS = ["127.0.0.1", "localhost", "testserver"]


def create_service_app(
    settings: Settings | None = None,
    *,
    db: Database | None = None,
    verifier: TokenVerifier | None = None,
    keys: Callable[[], ProviderKeys] = ProviderKeys,
    client_factory: ClientFactory = make_client,
) -> FastAPI:
    chosen = settings or get_settings()
    database = db or Database(chosen.database_url.get_secret_value())
    state = ServiceState(
        db=database,
        verifier=verifier or verifier_from(chosen),
        environment=chosen.environment,
        local_tokens=local_tokens_from(chosen),
        keys=keys,
        client_factory=client_factory,
        checkpointing=Checkpointing(chosen.database_url.get_secret_value()),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await state.close()
        await database.dispose()

    app = FastAPI(title="EnergyOps platform API", version="0.2.0", lifespan=lifespan)
    app.state.service = state
    if chosen.environment is Environment.LOCAL:
        # Stops a web page on another site from reaching the local API through DNS tricks.
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS)

    @app.middleware("http")
    async def never_cache(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(Forbidden)
    async def forbidden(_: Request, exc: Forbidden) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "environment": chosen.environment.value}

    app.include_router(routes_basic.router)
    app.include_router(routes_agents.router)
    app.include_router(routes_assistant.router)
    app.include_router(routes_tickets.router)
    app.include_router(routes_org.router)
    app.include_router(routes_admin.router)
    return app
