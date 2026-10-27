"""The local-only API. There is no sign-in yet, so it must never run anywhere but a developer's
own machine: the app refuses to be created outside the local environment, and `python -m
energyops.api` binds to 127.0.0.1 only. Requests whose Host header is not localhost are
refused too, which stops a web page on another site from reaching it through DNS tricks.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.middleware.trustedhost import TrustedHostMiddleware

from energyops.agents.energy_analyst import ENERGY_ANALYST_SPEC, AnalystRequest, EnergyAnalyst
from energyops.agents.energy_analyst_evals import good_reply
from energyops.agents.fact_sources import DirectFactSheets, McpFactSheets, McpTarget
from energyops.agents.runs import RunRecord, RunStatus
from energyops.agents.sources import ReadingsSource, synthetic_demo_source
from energyops.agents.spec import AgentSpec, load_prompt, unimplemented_guardrails
from energyops.api.providers import (
    ProviderId,
    ProviderInfo,
    ProviderUnavailable,
    make_client,
    provider_infos,
)
from energyops.api.store import RunStore
from energyops.config import Environment
from energyops.facts import build_fact_sheet
from energyops.models.base import ModelClient
from energyops.models.credentials import ProviderKeys

LOCAL_HOSTS = ["127.0.0.1", "localhost"]
AGENTS: dict[str, AgentSpec] = {"energy-analyst": ENERGY_ANALYST_SPEC}

# The playground page and its two files. A fixed list, so no other path can be asked for.
STATIC_DIR = Path(__file__).parent / "static"
PLAYGROUND_FILES = {"app.js": "playground.js", "style.css": "playground.css"}
PLAYGROUND_HEADERS = {
    # Only this server's own scripts and styles may run, and the page may only talk to this server.
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


class ApiStartupRefused(RuntimeError):
    """The API was asked to start somewhere it must not run."""


class ApiSettings(BaseSettings):
    # Only what the API needs. It does not touch the database yet, so it does not need one.
    model_config = SettingsConfigDict(env_prefix="ENERGYOPS_", env_file=".env", extra="ignore")
    environment: Environment = Environment.LOCAL


class AgentSummary(BaseModel):
    id: str
    name: str
    domain: str
    status: str
    description: str
    capabilities: tuple[str, ...]
    model_provider: str
    model_name: str
    prompt_version: str


class PromptView(BaseModel):
    version: str
    sha256: str
    text: str


class AgentDetail(BaseModel):
    summary: AgentSummary
    spec: dict[str, Any]
    prompt: PromptView
    guardrails_not_implemented: list[str]


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=500)
    site: str | None = Field(default=None, max_length=64)
    provider: ProviderId = "test"
    # "mcp": the agent calls the read-only energy MCP server (a subprocess). "direct": plain Python.
    data_access: Literal["mcp", "direct"] = "mcp"
    model: str | None = Field(default=None, min_length=1, max_length=100)


class RunSummary(BaseModel):
    run_id: str
    agent: str
    status: RunStatus
    question: str
    model_provider: str
    model_name: str
    attempts: int
    error_kind: str | None
    started_at: str
    finished_at: str


ClientFactory = Callable[[ProviderId, str | None, ProviderKeys, str], ModelClient]


def _summarise(agent_id: str, spec: AgentSpec) -> AgentSummary:
    return AgentSummary(
        id=agent_id,
        name=spec.name,
        domain=spec.domain,
        status=spec.status.value,
        description=spec.description,
        capabilities=spec.capabilities,
        model_provider=spec.config.model.provider,
        model_name=spec.config.model.name,
        prompt_version=spec.config.prompt_version,
    )


def _run_summary(record: RunRecord) -> RunSummary:
    return RunSummary(
        run_id=record.run_id,
        agent=record.agent,
        status=record.status,
        question=str(record.input.get("question", "")),
        model_provider=record.model_provider,
        model_name=record.model_name,
        attempts=record.attempts,
        error_kind=record.error_kind,
        started_at=record.started_at.isoformat(),
        finished_at=record.finished_at.isoformat(),
    )


def create_app(
    settings: ApiSettings | None = None,
    *,
    keys: ProviderKeys | None = None,
    source: ReadingsSource | None = None,
    store: RunStore | None = None,
    mcp_target: McpTarget | None = None,
    client_factory: ClientFactory = make_client,
) -> FastAPI:
    chosen = settings or ApiSettings()
    if chosen.environment is not Environment.LOCAL:
        raise ApiStartupRefused(
            f"This API has no sign-in and only runs in the local environment, "
            f"not '{chosen.environment.value}'."
        )
    readings = source or synthetic_demo_source()
    if mcp_target is None and source is not None:
        # A custom data source must reach the MCP path too, so serve it from an in-process server.
        from energyops.mcp_servers.energy import create_server

        mcp_target = create_server(source=source)
    runs = store or RunStore()
    provider_keys = keys

    def current_keys() -> ProviderKeys:
        # Read the .env file on each use so adding a key does not need a restart.
        return provider_keys if provider_keys is not None else ProviderKeys()

    app = FastAPI(title="EnergyOps local API", version="0.1.0")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS)

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/playground")

    @app.get("/playground", include_in_schema=False)
    def playground() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "playground.html", media_type="text/html", headers=PLAYGROUND_HEADERS
        )

    @app.get("/playground/{name}", include_in_schema=False)
    def playground_file(name: str) -> FileResponse:
        filename = PLAYGROUND_FILES.get(name)
        if filename is None:
            raise HTTPException(status_code=404, detail="Not found.")
        return FileResponse(STATIC_DIR / filename, headers=PLAYGROUND_HEADERS)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "environment": chosen.environment.value}

    @app.get("/providers")
    def providers() -> list[ProviderInfo]:
        return provider_infos(current_keys())

    @app.get("/agents")
    def list_agents() -> list[AgentSummary]:
        return [_summarise(agent_id, spec) for agent_id, spec in AGENTS.items()]

    def spec_for(agent_id: str) -> AgentSpec:
        spec = AGENTS.get(agent_id)
        if spec is None:
            raise HTTPException(status_code=404, detail=f"No agent called '{agent_id}'.")
        return spec

    @app.get("/agents/{agent_id}")
    def agent_detail(agent_id: str) -> AgentDetail:
        spec = spec_for(agent_id)
        prompt = load_prompt(spec)
        return AgentDetail(
            summary=_summarise(agent_id, spec),
            spec=spec.model_dump(mode="json"),
            prompt=PromptView(version=prompt.version, sha256=prompt.sha256, text=prompt.text),
            guardrails_not_implemented=unimplemented_guardrails(spec),
        )

    @app.post("/agents/{agent_id}/runs")
    async def run_agent(agent_id: str, body: RunRequest) -> RunRecord:
        """Run the agent once. A run that fails its checks is still a 200 with status 'failed'."""
        spec = spec_for(agent_id)
        try:
            test_reply = good_reply(build_fact_sheet(readings(body.site)))
        except (LookupError, ValueError):
            test_reply = "{}"  # the agent will report the unknown site or unusable data itself
        try:
            client = client_factory(body.provider, body.model, current_keys(), test_reply)
        except ProviderUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        facts = (
            McpFactSheets(spec, mcp_target)
            if body.data_access == "mcp"
            else DirectFactSheets(readings)
        )
        record = await EnergyAnalyst(spec, client, facts).run(
            AnalystRequest(question=body.question, site=body.site)
        )
        runs.add(record)
        return record

    @app.get("/runs")
    def list_runs(
        agent: str | None = None, limit: int = Query(default=50, ge=1, le=200)
    ) -> list[RunSummary]:
        return [_run_summary(r) for r in runs.newest_first(agent, limit)]

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> RunRecord:
        record = runs.get(run_id)
        if record is None:
            raise HTTPException(
                status_code=404, detail="No run with that id (runs are kept in memory)."
            )
        return record

    return app
