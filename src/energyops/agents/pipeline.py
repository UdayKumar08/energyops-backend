"""The pipeline every grounded energy agent shares.

A fixed sequence, not a model choosing tools: check the input, fetch the fact sheet, ask the
model once for a JSON answer, then check the shape and every figure in Python before anything
is returned. A rejected answer is sent back with the reasons, up to the spec's max_iterations
attempts. An agent supplies only the parts that differ: what to show the model, the shape of
its answer, and which pieces of text and fact ids in that answer must be checked.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from energyops.agents.fact_sources import ToolAccessError
from energyops.agents.runs import RunRecord, RunStatus, TraceRecorder, UsageTotals
from energyops.agents.spec import AgentSpec, load_prompt, unimplemented_guardrails
from energyops.calculations import EnergyDataError
from energyops.facts import FactSheet
from energyops.models import Message, ModelClient, ModelError, ModelRequest


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentRequest(Strict):
    """What every agent is asked: which site. The signed-in user will limit this later."""

    site: str | None = Field(default=None, max_length=64)


class ReplyShapeError(ValueError):
    """The reply could not be turned into the agent's output type. The message says why."""


def _describe(error: ValidationError) -> str:
    parts = []
    for item in error.errors():
        where = ".".join(str(p) for p in item["loc"]) or "reply"
        parts.append(f"{where}: {item['msg']}")
    return "; ".join(parts[:5])


def parse_json_reply[T: BaseModel](text: str, output_type: type[T], shape: str) -> T:
    """Parse a model reply into `output_type`. `shape` is a one-line example for error messages."""
    body = text.strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", body).strip()
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end < start:
        raise ReplyShapeError(f"the reply was not a single JSON object like {shape}")
    candidate = body[start : end + 1]
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        if exc.pos >= len(candidate):
            # Seen in a real run: a model left off the final closing brace. The parser's own
            # message ("Expecting ',' delimiter") does not tell a model what to fix.
            raise ReplyShapeError(
                "the reply was not valid JSON (it ends before the object is closed, so check "
                "that every [ and { has its closing ] and })"
            ) from exc
        raise ReplyShapeError(f"the reply was not valid JSON ({exc.msg})") from exc
    try:
        return output_type.model_validate(data)
    except ValidationError as exc:
        raise ReplyShapeError(f"the JSON had the wrong shape ({_describe(exc)})") from exc


def blocked(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms if term)


RETRY_INSTRUCTION = (
    "Answer again with only the JSON object, using only figures from the fact sheet."
)


class HasVia(Protocol):
    """Whatever an agent fetches its facts with says which path it took, for the trace."""

    via: str


class GroundedAgent[RequestT: AgentRequest, OutputT: BaseModel, ProviderT: HasVia]:
    """Subclasses fill in the hooks below. `run` is the same for every agent.

    A lookup error that carries an `error_kind` attribute is recorded under that kind, so an
    agent can report "unknown_anomaly" instead of the default "unknown_site".
    """

    def __init__(self, spec: AgentSpec, model: ModelClient, facts: ProviderT) -> None:
        missing = unimplemented_guardrails(spec)
        if missing:
            raise ValueError(f"guardrails not implemented yet: {', '.join(missing)}")
        self.spec = spec
        self._model = model
        self._facts = facts
        self._prompt = load_prompt(spec)

    # ------------------------------------------------------------------ hooks

    async def fetch_facts(self, request: RequestT, trace: TraceRecorder) -> FactSheet:
        """Get the fact sheet this agent will show the model, through its provider."""
        raise NotImplementedError

    def input_text(self, request: RequestT) -> str:
        """Free text from the person, checked against the blocklist. Empty when there is none."""
        raise NotImplementedError

    def user_message(self, request: RequestT, sheet: FactSheet) -> str:
        raise NotImplementedError

    def parse(self, reply: str) -> OutputT:
        """Turn the reply into the output type, or raise ReplyShapeError."""
        raise NotImplementedError

    def grounding_problems(self, output: OutputT, sheet: FactSheet) -> list[str]:
        """Every figure and fact id in the output that the fact sheet does not back."""
        raise NotImplementedError

    # ------------------------------------------------------------------ the run

    def _check(self, reply: str, sheet: FactSheet) -> tuple[OutputT | None, str, list[str]]:
        """Return the parsed output (if the shape is right), a problem kind and the problems."""
        try:
            output = self.parse(reply)
        except ReplyShapeError as exc:
            return None, "invalid_output", [str(exc)]
        problems = self.grounding_problems(output, sheet)
        return output, "grounding" if problems else "", problems

    async def run(self, request: RequestT) -> RunRecord:
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
        is_blocked = blocked(self.input_text(request), terms)
        trace.step("check_input", started, blocked=is_blocked)
        if is_blocked:
            return finish(
                RunStatus.FAILED,
                error_kind="blocklist_input",
                error="the question contains a blocked term",
            )

        started = time.perf_counter()
        try:
            async with asyncio.timeout(config.timeout_seconds):
                sheet = await self.fetch_facts(request, trace)
        except TimeoutError:
            trace.step("fact_sheet", started, via=self._facts.via, error="timeout")
            return finish(
                RunStatus.FAILED,
                error_kind="tool_timeout",
                error=f"no fact sheet within {config.timeout_seconds} seconds",
            )
        except LookupError as exc:
            trace.step("fact_sheet", started, via=self._facts.via, error=str(exc))
            return finish(
                RunStatus.FAILED,
                error_kind=getattr(exc, "error_kind", "unknown_site"),
                error=str(exc),
            )
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
            Message("user", self.user_message(request, sheet)),
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
                        + ". "
                        + RETRY_INSTRUCTION,
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
            output, kind, problems = self._check(response.text, sheet)
            trace.step("validate_output", started, attempt=attempt, kind=kind, problems=problems)
            if output is not None and not problems:
                if blocked(response.text, terms):
                    return finish(
                        RunStatus.FAILED,
                        error_kind="blocklist_output",
                        error="the answer contains a blocked term",
                    )
                return finish(RunStatus.SUCCEEDED, output=output.model_dump())

        return finish(RunStatus.FAILED, error_kind=kind, error="; ".join(problems))
