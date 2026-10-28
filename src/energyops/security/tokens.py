"""Checking who a request comes from.

A token only proves who the person is. It is never trusted for anything else: the organisation,
the role and the sites a person may see always come from our own database (see auth.py), so a
token cannot grant itself more.

Two kinds of token exist, and a deployment accepts exactly one.

Local development: the backend signs a short token itself for a seeded user, with a secret from
the local .env. This is accepted only in the local environment, where the settings already refuse
to hold such a secret anywhere else.

Everywhere else: access tokens issued by Microsoft Entra ID for this application. The signature is
checked against the tenant's published keys with RS256 only (so a token signed with a shared
secret, or with no signature at all, is refused), and so are the issuer, which must be exactly
this tenant's v2 issuer, the tenant id, the audience, the expiry and the required scope.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Protocol

import jwt
from pydantic import SecretStr

LOCAL_ISSUER = "energyops-local"
LOCAL_AUDIENCE = "energyops-local"
CLOCK_SKEW_SECONDS = 60


class TokenError(Exception):
    """The token is missing, malformed, expired or not for us. The message is safe to show."""


@dataclass(frozen=True, slots=True)
class Claims:
    """What a verified token says about the person. Nothing here decides access."""

    subject: str
    email: str | None
    name: str | None
    external_id: str | None  # Entra's stable object id (`oid`); None for local tokens


class TokenVerifier(Protocol):
    async def verify(self, token: str) -> Claims: ...


# ------------------------------------------------------------------ local development


class LocalTokens:
    """Signs and checks the development tokens. Only the local environment creates this."""

    def __init__(self, secret: SecretStr) -> None:
        value = secret.get_secret_value()
        if len(value) < 32:
            raise ValueError("ENERGYOPS_LOCAL_JWT_SECRET must be at least 32 characters")
        self._secret = value

    def issue(self, email: str, *, minutes: int = 60) -> str:
        now = int(time.time())
        payload = {
            "iss": LOCAL_ISSUER,
            "aud": LOCAL_AUDIENCE,
            "sub": email,
            "email": email,
            "iat": now,
            "exp": now + minutes * 60,
        }
        return jwt.encode(payload, self._secret, algorithm="HS256")

    async def verify(self, token: str) -> Claims:
        try:
            data = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                audience=LOCAL_AUDIENCE,
                issuer=LOCAL_ISSUER,
                options={"require": ["exp", "iss", "aud", "sub"]},
                leeway=CLOCK_SKEW_SECONDS,
            )
        except jwt.PyJWTError as exc:
            raise TokenError(_reason(exc)) from None
        return Claims(
            subject=str(data["sub"]), email=data.get("email"), name=None, external_id=None
        )


# ------------------------------------------------------------------ Microsoft Entra ID


class KeyProvider(Protocol):
    """Finds the public key that signed a token (by the `kid` in its header)."""

    def signing_key(self, token: str) -> Any: ...


class JwksKeyProvider:
    """Fetches the tenant's published signing keys and caches them."""

    def __init__(self, jwks_uri: str, *, cache_seconds: int = 3600) -> None:
        self._client = jwt.PyJWKClient(jwks_uri, cache_keys=True, lifespan=cache_seconds)

    def signing_key(self, token: str) -> Any:
        return self._client.get_signing_key_from_jwt(token).key


def entra_jwks_uri(tenant_id: str) -> str:
    return f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"


class EntraTokens:
    def __init__(
        self,
        *,
        tenant_id: str,
        client_id: str,
        keys: KeyProvider | None = None,
        required_scope: str = "access_as_user",
    ) -> None:
        if not tenant_id or not client_id:
            raise ValueError("an Entra tenant id and client id are both required")
        self._tenant = tenant_id
        self._audiences = [client_id, f"api://{client_id}"]
        self._issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        self._scope = required_scope
        self._keys = keys or JwksKeyProvider(entra_jwks_uri(tenant_id))

    async def verify(self, token: str) -> Claims:
        try:
            # Fetching keys can block on the network, so it runs off the event loop.
            key = await asyncio.to_thread(self._keys.signing_key, token)
            data = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self._audiences,
                issuer=self._issuer,
                options={"require": ["exp", "iss", "aud", "tid", "oid"]},
                leeway=CLOCK_SKEW_SECONDS,
            )
        except jwt.PyJWTError as exc:
            raise TokenError(_reason(exc)) from None
        if data["tid"] != self._tenant:
            raise TokenError("The token is for a different tenant.")
        if data.get("ver") != "2.0":
            raise TokenError("Only version 2.0 access tokens are accepted.")
        if self._scope not in str(data.get("scp", "")).split():
            raise TokenError(f"The token does not carry the {self._scope} scope.")
        email = data.get("preferred_username") or data.get("email")
        return Claims(
            subject=str(data["oid"]),
            email=str(email) if email else None,
            name=data.get("name"),
            external_id=str(data["oid"]),
        )


def _reason(exc: jwt.PyJWTError) -> str:
    if isinstance(exc, jwt.ExpiredSignatureError):
        return "The token has expired."
    if isinstance(exc, jwt.InvalidAudienceError):
        return "The token is not for this application."
    if isinstance(exc, jwt.InvalidIssuerError):
        return "The token comes from an issuer we do not accept."
    if isinstance(exc, jwt.MissingRequiredClaimError):
        return f"The token is missing the {exc.claim} claim."
    return "The token could not be verified."
