"""Request dependencies: who is calling, and whether their role allows what they ask for."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from energyops.security.auth import NotProvisioned, authenticate
from energyops.security.permissions import Permission
from energyops.security.principal import Principal
from energyops.security.tokens import TokenError
from energyops.service.state import ServiceState

_bearer = HTTPBearer(auto_error=False)
log = logging.getLogger(__name__)


def state_of(request: Request) -> ServiceState:
    state: ServiceState = request.app.state.service
    return state


async def current_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    state = state_of(request)
    if credentials is None:
        raise HTTPException(
            status_code=401, detail="Sign in first.", headers={"WWW-Authenticate": "Bearer"}
        )
    try:
        claims = await state.verifier.verify(credentials.credentials)
    except TokenError as exc:
        # The reason names a rule (wrong issuer, missing scope, old token version), never the
        # token itself, and it is what an operator needs when sign-in is refused.
        log.warning("Sign-in refused: %s", exc)
        raise HTTPException(
            status_code=401, detail=str(exc), headers={"WWW-Authenticate": "Bearer"}
        ) from None
    try:
        return await authenticate(state.db, claims)
    except NotProvisioned as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None


def needs(permission: Permission) -> Callable[[Principal], Awaitable[Principal]]:
    """A dependency that signs the caller in and checks their role allows `permission`."""

    async def check(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
        principal.require(permission)  # a Forbidden becomes a 403 in app.py
        return principal

    return check
