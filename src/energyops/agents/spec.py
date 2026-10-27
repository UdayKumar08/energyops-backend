"""How an agent is declared. The fields mirror the tabs of an admin registry screen:
Basic (name, domain, status, capabilities, description), Configuration (model, temperature,
max iterations, MCP servers, prompt) and Guardrails.

A spec is only a declaration. Declaring a guardrail does not make it exist: the runner refuses
to start an agent whose spec switches on a guardrail that is not implemented yet.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from importlib import resources
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_TAG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_PROMPT_FILE = re.compile(r"^[a-z_]+/v[0-9]+\.md$")


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AgentStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    DISABLED = "disabled"


class ModelChoice(_Frozen):
    provider: str = Field(min_length=1)
    name: str = Field(min_length=1)


class Guardrail(_Frozen):
    enabled: bool = False


class BlocklistGuardrail(_Frozen):
    enabled: bool = False
    terms: tuple[str, ...] = ()


class GuardrailsConfig(_Frozen):
    content_safety: Guardrail = Guardrail()
    blocklist: BlocklistGuardrail = BlocklistGuardrail()
    prompt_shield: Guardrail = Guardrail()
    # Always on and not switchable: every number an agent returns is checked against the facts.
    grounding_check: Literal[True] = True


class AgentConfig(_Frozen):
    model: ModelChoice
    prompt_file: str
    prompt_version: str
    temperature: float = Field(default=0.0, ge=0, le=2)
    # For a fixed pipeline this is the number of model attempts, first try included.
    max_iterations: int = Field(default=2, ge=1, le=5)
    mcp_servers: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    max_output_tokens: int = Field(default=1024, ge=1, le=8192)

    @field_validator("prompt_file")
    @classmethod
    def _safe_prompt_file(cls, value: str) -> str:
        if not _PROMPT_FILE.match(value):
            raise ValueError("prompt_file must look like energy_analyst/v1.md")
        return value


class AgentSpec(_Frozen):
    name: str = Field(min_length=3, max_length=80)
    domain: str = Field(min_length=1)
    status: AgentStatus = AgentStatus.DRAFT
    capabilities: tuple[str, ...] = Field(min_length=1)
    description: str = Field(min_length=1, max_length=500)
    config: AgentConfig
    guardrails: GuardrailsConfig = GuardrailsConfig()

    @field_validator("capabilities")
    @classmethod
    def _tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not _TAG.match(tag) for tag in value):
            raise ValueError("capabilities must be lowercase tags like peak-demand")
        if len(set(value)) != len(value):
            raise ValueError("capabilities must not repeat")
        return value


@dataclass(frozen=True, slots=True)
class LoadedPrompt:
    text: str
    version: str
    sha256: str


def load_prompt(spec: AgentSpec) -> LoadedPrompt:
    """Read the versioned prompt file the spec points to and fingerprint it."""
    folder, name = spec.config.prompt_file.split("/")
    path = resources.files("energyops.agents") / "prompts" / folder / name
    text = path.read_text(encoding="utf-8")
    return LoadedPrompt(
        text=text,
        version=spec.config.prompt_version,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def unimplemented_guardrails(spec: AgentSpec) -> list[str]:
    """Guardrails the spec switches on that this codebase cannot enforce yet."""
    missing = []
    if spec.guardrails.content_safety.enabled:
        missing.append("content_safety")
    if spec.guardrails.prompt_shield.enabled:
        missing.append("prompt_shield")
    return missing
