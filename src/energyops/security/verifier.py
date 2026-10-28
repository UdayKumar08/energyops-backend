"""Choosing how this deployment checks sign-in tokens, from its settings."""

from __future__ import annotations

from energyops.config import ConfigurationError, Environment, Settings
from energyops.security.tokens import EntraTokens, LocalTokens, TokenVerifier


def local_tokens_from(settings: Settings) -> LocalTokens | None:
    """Development tokens, only in the local environment and only when a secret is set."""
    if settings.environment is Environment.LOCAL and settings.local_jwt_secret is not None:
        return LocalTokens(settings.local_jwt_secret)
    return None


def verifier_from(settings: Settings) -> TokenVerifier:
    local = local_tokens_from(settings)
    if local is not None:
        return local
    if settings.environment is Environment.LOCAL:
        raise ConfigurationError(
            "Set ENERGYOPS_LOCAL_JWT_SECRET (at least 32 characters) in the local .env to sign in."
        )
    if not settings.entra_tenant_id or not settings.entra_client_id:
        raise ConfigurationError(
            "Set ENERGYOPS_ENTRA_TENANT_ID and ENERGYOPS_ENTRA_CLIENT_ID to check sign-in tokens."
        )
    return EntraTokens(tenant_id=settings.entra_tenant_id, client_id=settings.entra_client_id)
