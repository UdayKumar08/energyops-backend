"""Shared helpers for tests of the platform API."""

from __future__ import annotations

from typing import Any

from pydantic import SecretStr

from conftest import APP_URL
from energyops.config import Settings
from energyops.db.session import Database
from energyops.security.tokens import LocalTokens
from energyops.service.app import create_service_app

SECRET = SecretStr("s" * 48)
TOKENS = LocalTokens(SECRET)

ADMIN = "admin@harbour.example"
MANAGER = "manager@harbour.example"
TECH = "tech@harbour.example"  # limited to demo-hotel
VIEWER = "viewer@harbour.example"
NORTH = "admin@northside.example"
PLATFORM = "platform@energyops.example"


def settings() -> Settings:
    return Settings(database_url=SecretStr(APP_URL), local_jwt_secret=SECRET)


def make_app(db: Database, **kwargs: Any) -> Any:
    return create_service_app(settings(), db=db, **kwargs)


def as_user(email: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS.issue(email)}"}
