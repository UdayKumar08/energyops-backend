"""Provider API keys, read from the environment or a local .env file.

Keys are held as SecretStr so they never appear in a repr, log line or traceback by accident.
"""

from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderKeys(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gemini_api_key: SecretStr | None = None
    nebius_api_key: SecretStr | None = None
