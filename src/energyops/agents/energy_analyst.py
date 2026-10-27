"""The energy analyst agent.

A fixed pipeline, not a model choosing tools: Python builds the fact sheet, one model call
explains it as JSON, and Python checks the shape and every figure before anything is returned.
A rejected answer is sent back once with the reasons, up to the spec's max_iterations attempts.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from energyops.agents.fact_sources import FactSheetProvider, ToolAccessError
from energyops.agents.grounding import check_fact_ids, check_text
from energyops.agents.runs import RunRecord, RunStatus, TraceRecorder, UsageTotals
from energyops.agents.spec import (
    AgentConfig,
    AgentSpec,
    ModelChoice,
    load_prompt,
    unimplemented_guardrails,
)
from energyops.calculations import EnergyDataError
from energyops.facts import FactSheet
from energyops.models import Message, ModelClient, ModelError, ModelRequest
from energyops.models.providers import DEFAULT_GEMINI_MODEL


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalystRequest(_Strict):
    question: str = Field(min_length=1, max_length=500)
    site: str | None = Field(default=None, max_length=64)


class Finding(_Strict):
    text: str = Field(min_length=1, max_length=400)
    fact_ids: list[str] = Field(min_length=1, max_length=8)


class AnalystReport(_Strict):
    summary: str = Field(min_length=1, max_length=800)
    findings: list[Finding] = Field(default_factory=list, max_length=6)


ENERGY_ANALYST_SPEC = AgentSpec(
    name="EnergyOps Energy Analyst",
    domain="energy-analysis",
    capabilities=(
        "energy-analysis",
        "consumption-summary",
        "peak-demand",
        "weekday-weekend-comparison",
    ),
    description="Explains how a site used energy over a period using computed facts only.",
    config=AgentConfig(
        model=ModelChoice(provider="google-gemini", name=DEFAULT_GEMINI_MODEL),
        prompt_file="energy_analyst/v3.md",
        prompt_version="v3",
        max_iterations=2,
        mcp_servers=("energyops-energy",),
        allowed_tools=("list_sites", "get_fact_sheet"),
    ),
)


class ReplyShapeError(ValueError):
    """The reply could not be turned into an AnalystReport. The message says why."""


def _describe(error: ValidationError) -> str:
    parts = []
    for item in error.errors():
        where = ".".join(str(p) for p in item["loc"]) or "reply"
        parts.append(f"{where}: {item['msg']}")
    return "; ".join(parts[:5])


def _parse_report(text: str) -> AnalystReport:
    body = text.strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", body).strip()
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end < start:
        raise ReplyShapeError(
            'the reply was not a single JSON object like {"summary": "...", "findings": []}'
        )
    try:
        data = json.loads(body[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ReplyShapeError(f"the reply was not valid JSON ({exc.msg})") from exc
    try:
        return AnalystReport.model_validate(data)
    except ValidationError as exc:
        raise ReplyShapeError(f"the JSON had the wrong shape ({_describe(exc)})") from exc


def _blocked(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms if term)


class EnergyAnalyst:
    def __init__(self, spec: AgentSpec, model: ModelClient, facts: FactSheetProvider) -> None:
        missing = unimplemented_guardrails(spec)
        if missing:
            raise ValueError(f"guardrails not implemented yet: {', '.join(missing)}")
        self.spec = spec
        self._model = model
        self._facts = facts
        self._prompt = load_prompt(spec)

    def _check(self, reply: str, sheet: FactSheet) -> tuple[AnalystReport | None, str, list[str]]:
        """Return the parsed report (if the shape is right), a problem kind and the problems."""
        try:
            report = _parse_report(reply)
        except ReplyShapeError as exc:
            return None, "invalid_output", [str(exc)]
        problems = check_text(report.summary, sheet)
        for finding in report.findings:
            problems += check_text(finding.text, sheet)
            problems += check_fact_ids(finding.fact_ids, sheet)
        return report, "grounding" if problems else "", problems

    async def run(self, request: AnalystRequest) -> RunRecord:
        trace = TraceRecorder()
        config = self.spec.config
        usage = UsageTotals()
        attempts = 0

        def finish(
            status: RunStatus,
            *,
            output: dict[str, object] | None = None,
            error_kind: str | None = None,
            error: str | None = None,
        ) -> RunRecord:
            return RunRecord(
                run_id=trace.run_id,
                agent=self.spec.name,
                prompt_version=self._prompt.version,
                prompt_sha256=self._prompt.sha256,
                model_provider=self._model.provider,
                model_name=self._model.model,
                status=status,
                started_at=trace.started_at,
                finished_at=datetime.now(UTC),
                input=request.model_dump(),
                output=output,
                error_kind=error_kind,
                error=error,
                attempts=attempts,
                usage=usage,
                steps=trace.steps,
            )

        terms = (
            self.spec.guardrails.blocklist.terms if self.spec.guardrails.blocklist.enabled else ()
        )

        started = time.perf_counter()
        blocked = _blocked(request.question, terms)
        trace.step("check_input", started, blocked=blocked)
        if blocked:
            return finish(
                RunStatus.FAILED,
                error_kind="blocklist_input",
                error="the question contains a blocked term",
            )

        started = time.perf_counter()
        try:
            async with asyncio.timeout(config.timeout_seconds):
                sheet = await self._facts(request.site, trace)
        except TimeoutError:
            trace.step("fact_sheet", started, via=self._facts.via, error="timeout")
            return finish(
                RunStatus.FAILED,
                error_kind="tool_timeout",
                error=f"no fact sheet within {config.timeout_seconds} seconds",
            )
        except LookupError as exc:
            trace.step("fact_sheet", started, via=self._facts.via, error=str(exc))
            return finish(RunStatus.FAILED, error_kind="unknown_site", error=str(exc))
        except EnergyDataError as exc:
            trace.step("fact_sheet", started, via=self._facts.via, error=str(exc))
            return finish(RunStatus.FAILED, error_kind="unusable_data", error=str(exc))
        except ToolAccessError as exc:
            trace.step("fact_sheet", started, via=self._facts.via, error=str(exc))
            return finish(RunStatus.FAILED, error_kind="tool_error", error=str(exc))
        trace.step(
            "fact_sheet",
            started,
            via=self._facts.via,
            facts=[
                {"id": f.id, "key": f.key, "display": f.display, "description": f.description}
                for f in sheet.facts
            ],
            warnings=list(sheet.warnings),
        )

        messages = [
            Message("system", self._prompt.text),
            Message(
                "user",
                f"Question: {request.question}\n\n"
                f"Fact sheet (the only figures you may quote):\n{sheet.to_prompt_text()}",
            ),
        ]
        last_reply = ""
        kind = "invalid_output"
        problems: list[str] = []

        for attempt in range(1, config.max_iterations + 1):
            attempts = attempt
            if problems:
                messages += [
                    Message("assistant", last_reply),
                    Message(
                        "user",
                        "Your answer was rejected: "
                        + "; ".join(problems)
                        + ". Answer again with only the JSON object, using only figures "
                        "from the fact sheet.",
                    ),
                ]
            started = time.perf_counter()
            try:
                async with asyncio.timeout(config.timeout_seconds):
                    response = await self._model.complete(
                        ModelRequest(
                            messages=tuple(messages),
                            max_output_tokens=config.max_output_tokens,
                            temperature=config.temperature,
                            timeout_seconds=config.timeout_seconds,
                        )
                    )
            except TimeoutError:
                trace.step("model_call", started, attempt=attempt, error="timeout")
                return finish(
                    RunStatus.FAILED,
                    error_kind="timeout",
                    error=f"no answer within {config.timeout_seconds} seconds",
                )
            except ModelError as exc:
                trace.step(
                    "model_call",
                    started,
                    attempt=attempt,
                    error=type(exc).__name__,
                    retryable=exc.retryable,
                )
                return finish(
                    RunStatus.FAILED, error_kind=f"model_error:{type(exc).__name__}", error=str(exc)
                )

            usage.input_tokens += response.usage.input_tokens
            usage.output_tokens += response.usage.output_tokens
            usage.estimated = usage.estimated or response.usage.estimated
            trace.step(
                "model_call",
                started,
                attempt=attempt,
                provider=response.provider,
                model=response.model,
                finish_reason=response.finish_reason,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                reply=response.text,
            )

            started = time.perf_counter()
            last_reply = response.text
            report, kind, problems = self._check(response.text, sheet)
            trace.step("validate_output", started, attempt=attempt, kind=kind, problems=problems)
            if report is not None and not problems:
                if _blocked(response.text, terms):
                    return finish(
                        RunStatus.FAILED,
                        error_kind="blocklist_output",
                        error="the answer contains a blocked term",
                    )
                return finish(RunStatus.SUCCEEDED, output=report.model_dump())

        return finish(RunStatus.FAILED, error_kind=kind, error="; ".join(problems))
