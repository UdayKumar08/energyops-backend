"""Application settings, read from environment variables and an optional .env file.

Every setting uses the ENERGYOPS_ prefix. Secrets are SecretStr so they are hidden when a
settings object is printed or logged. Combinations that would be unsafe are refused at
start-up instead of being discovered later.
"""

import re
from enum import StrEnum
from functools import lru_cache
from typing import Literal, Self
from urllib.parse import urlparse

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    DEV = "dev"
    NONPROD = "nonprod"
    PROD = "prod"


class DataMode(StrEnum):
    DEMO = "demo"
    REPLAY = "replay"
    LIVE = "live"


# "prod" as a whole word inside a host name, such as energyops-prod.example.com.
# It deliberately does not match words that merely contain it, such as "reproduction".
_PROD_IN_HOST = re.compile(r"(^|[-.])prod([-.]|$)")


class ConfigurationError(ValueError):
    """Raised when settings are missing or form an unsafe combination."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ENERGYOPS_", env_file=".env", extra="ignore")

    environment: Environment = Environment.LOCAL
    data_mode: DataMode = DataMode.DEMO
    # The application's connection: a restricted role that owns nothing, so row level security
    # applies to it.
    database_url: SecretStr
    # The owner role, used only to run migrations and the seed script. Never by the running API.
    admin_database_url: SecretStr | None = None
    local_jwt_secret: SecretStr | None = None
    # Microsoft Entra ID, used to check sign-in tokens outside the local environment.
    entra_tenant_id: str | None = None
    entra_client_id: str | None = None
    # An OpenAI-compatible embeddings endpoint for knowledge search. The key is read from the
    # environment only. Without all three, non-production environments use a lexical stand-in.
    # "gemini" uses Google's own method (the base URL is then optional); "openai_compatible"
    # needs the base URL.
    embeddings_provider: Literal["openai_compatible", "gemini"] = "openai_compatible"
    embeddings_base_url: str | None = None
    embeddings_api_key: SecretStr | None = None
    embeddings_model: str | None = None
    # Ask the provider for 768 numbers per embedding. Turn off for a provider that rejects the
    # `dimensions` field; the answer's size is checked either way.
    embeddings_send_dimensions: bool = True
    log_level: str = "INFO"

    @property
    def database_host(self) -> str:
        return urlparse(self.database_url.get_secret_value()).hostname or ""

    @model_validator(mode="after")
    def refuse_unsafe_combinations(self) -> Self:
        problems: list[str] = []
        if self.environment is Environment.PROD and self.data_mode is DataMode.DEMO:
            problems.append("ENERGYOPS_DATA_MODE=demo is not allowed in the prod environment.")
        if self.environment is not Environment.PROD and _PROD_IN_HOST.search(self.database_host):
            problems.append(
                f"Database host '{self.database_host}' looks like a production server, "
                f"but the environment is '{self.environment.value}'."
            )
        if self.environment is not Environment.LOCAL and self.local_jwt_secret is not None:
            problems.append("ENERGYOPS_LOCAL_JWT_SECRET is only allowed in the local environment.")
        if problems:
            raise ConfigurationError(" ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    # database_url is required and is read from the environment, which mypy cannot see.
    return Settings()  # type: ignore[call-arg]
