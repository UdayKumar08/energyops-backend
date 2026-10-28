"""What the running service holds, built once at start-up (reached via `request.app.state`)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from energyops.api.providers import ProviderId, make_client
from energyops.config import Environment
from energyops.db.checkpoint import Checkpointing
from energyops.db.session import Database
from energyops.models.base import ModelClient
from energyops.models.credentials import ProviderKeys
from energyops.security.tokens import LocalTokens, TokenVerifier

ClientFactory = Callable[[ProviderId, str | None, ProviderKeys, str], ModelClient]


@dataclass
class ServiceState:
    db: Database
    verifier: TokenVerifier
    environment: Environment
    # Present only in the local environment, where it signs development tokens.
    local_tokens: LocalTokens | None = None
    keys: Callable[[], ProviderKeys] = ProviderKeys
    client_factory: ClientFactory = make_client
    # The approval graph's saved state; opened on first use.
    checkpointing: Checkpointing | None = None

    async def close(self) -> None:
        if self.checkpointing is not None:
            await self.checkpointing.close()
