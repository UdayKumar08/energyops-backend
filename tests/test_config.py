import os

import pytest
from pydantic import ValidationError

from energyops.config import DataMode, Environment, Settings

DB = "postgresql+psycopg://energyops_app:s3cretpw@localhost:5432/energyops_local"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove any real ENERGYOPS_ variables so tests never depend on the developer's machine."""
    for name in list(os.environ):
        if name.startswith("ENERGYOPS_"):
            monkeypatch.delenv(name)


def test_valid_local_settings_load_with_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENERGYOPS_DATABASE_URL", DB)
    settings = Settings(_env_file=None)
    assert settings.environment is Environment.LOCAL
    assert settings.data_mode is DataMode.DEMO
    assert settings.database_host == "localhost"


def test_missing_database_url_is_an_error() -> None:
    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None)


def test_secrets_do_not_appear_when_printed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENERGYOPS_DATABASE_URL", DB)
    monkeypatch.setenv("ENERGYOPS_LOCAL_JWT_SECRET", "topsecretvalue")
    settings = Settings(_env_file=None)
    text = repr(settings) + str(settings)
    assert "s3cretpw" not in text
    assert "topsecretvalue" not in text


def test_invalid_environment_name_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENERGYOPS_DATABASE_URL", DB)
    monkeypatch.setenv("ENERGYOPS_ENVIRONMENT", "staging")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_demo_data_is_refused_in_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENERGYOPS_DATABASE_URL", "postgresql+psycopg://u:p@db.example.com/x")
    monkeypatch.setenv("ENERGYOPS_ENVIRONMENT", "prod")
    monkeypatch.setenv("ENERGYOPS_DATA_MODE", "demo")
    with pytest.raises(ValidationError, match="not allowed in the prod environment"):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    ("host", "allowed"),
    [
        ("energyops-prod.postgres.database.azure.com", False),
        ("prod.db.example.com", False),
        ("energyops-dev.postgres.database.azure.com", True),
        ("reproduction.example.com", True),
        ("localhost", True),
    ],
)
def test_production_looking_database_host_is_refused_outside_prod(
    monkeypatch: pytest.MonkeyPatch, host: str, allowed: bool
) -> None:
    monkeypatch.setenv("ENERGYOPS_DATABASE_URL", f"postgresql+psycopg://u:p@{host}/x")
    monkeypatch.setenv("ENERGYOPS_ENVIRONMENT", "dev")
    if allowed:
        Settings(_env_file=None)
    else:
        with pytest.raises(ValidationError, match="looks like a production server"):
            Settings(_env_file=None)


def test_local_jwt_secret_is_refused_outside_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENERGYOPS_DATABASE_URL", DB)
    monkeypatch.setenv("ENERGYOPS_ENVIRONMENT", "dev")
    monkeypatch.setenv("ENERGYOPS_LOCAL_JWT_SECRET", "x")
    with pytest.raises(ValidationError, match="only allowed in the local environment"):
        Settings(_env_file=None)
