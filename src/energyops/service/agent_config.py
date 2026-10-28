"""Settings a platform admin may change for an agent, and how they are applied.

An agent's code, prompt text and checks are fixed in the repository. What an admin can change is
a short list of operating settings, each validated the same way the built-in spec is. The
overrides are stored in the database and applied on top of the built-in spec for every run, so
the spec in the repository stays the reviewed starting point and the override is the audit trail.

Content safety and prompt shield are not here on purpose: they are not implemented, and a switch
that does nothing would be a false assurance.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from energyops.agents.spec import AgentSpec, AgentStatus

ProviderName = Literal["test", "gemini", "nebius"]
SPEC_PROVIDERS = {"google-gemini": "gemini", "nebius": "nebius", "test-adapter": "test"}


class AgentOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: AgentStatus | None = None
    provider: ProviderName | None = None
    model: str | None = Field(default=None, min_length=1, max_length=100)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_iterations: int | None = Field(default=None, ge=1, le=5)
    timeout_seconds: float | None = Field(default=None, gt=0, le=300)
    blocklist_enabled: bool | None = None
    blocklist_terms: list[str] | None = Field(default=None, max_length=50)

    def stored(self) -> dict[str, Any]:
        """Only what was set, which is what the database keeps."""
        return self.model_dump(mode="json", exclude_none=True)


class InvalidOverrides(ValueError):
    """The stored or submitted settings cannot be applied."""


def parse_overrides(raw: dict[str, Any] | None) -> AgentOverrides:
    try:
        parsed = AgentOverrides.model_validate(raw or {})
    except ValidationError as exc:
        raise InvalidOverrides(str(exc.errors()[0]["msg"])) from None
    for term in parsed.blocklist_terms or ():
        if not term.strip() or len(term) > 100:
            raise InvalidOverrides("Each blocked term must be 1 to 100 characters.")
    return parsed


def effective_spec(spec: AgentSpec, raw: dict[str, Any] | None) -> AgentSpec:
    """The built-in spec with the admin's settings applied, validated as a whole."""
    overrides = parse_overrides(raw)
    data = spec.model_dump(mode="json")
    if overrides.status is not None:
        data["status"] = overrides.status.value
    config = data["config"]
    if overrides.provider is not None:
        config["model"]["provider"] = overrides.provider
    if overrides.model is not None:
        config["model"]["name"] = overrides.model
    if overrides.temperature is not None:
        config["temperature"] = overrides.temperature
    if overrides.max_iterations is not None:
        config["max_iterations"] = overrides.max_iterations
    if overrides.timeout_seconds is not None:
        config["timeout_seconds"] = overrides.timeout_seconds
    blocklist = data["guardrails"]["blocklist"]
    if overrides.blocklist_enabled is not None:
        blocklist["enabled"] = overrides.blocklist_enabled
    if overrides.blocklist_terms is not None:
        blocklist["terms"] = [t.strip() for t in overrides.blocklist_terms]
    try:
        return AgentSpec.model_validate(data)
    except ValidationError as exc:
        raise InvalidOverrides(str(exc.errors()[0]["msg"])) from None


def provider_for(spec: AgentSpec) -> ProviderName | None:
    """The provider id a spec points to, or None when it names one this build cannot run."""
    name = spec.config.model.provider
    if name in ("test", "gemini", "nebius"):
        return name  # type: ignore[return-value]
    return SPEC_PROVIDERS.get(name)  # type: ignore[return-value]
