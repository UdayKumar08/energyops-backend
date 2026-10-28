"""The supervisor: it reads one message and sends it to the right part of the product.

It is a LangGraph graph with a fixed shape, not a model deciding what to do next:

    check the message  ->  ask the model for a route (a label)  ->  check the route  ->  act

The model's whole power is to pick one label from a closed list and give a one-sentence reason.
Python checks the label, checks that the person's role allows it and that it has what it needs,
and then runs the matching agent or capability. Whatever the user reads is either an agent's
checked answer or a fixed sentence written here. A wrong or manipulated choice can therefore
send a question to the wrong agent, which still answers only from checked facts and only within
the person's permissions, but it cannot make the product do anything the person could not
already do.

Prompt shield and content safety are not implemented (see docs/agents.md). The blocklist is.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from pydantic import Field

from energyops.agents.pipeline import (
    AgentRequest,
    ReplyShapeError,
    Strict,
    blocked,
    parse_json_reply,
)
from energyops.agents.registry_types import RunInputs
from energyops.agents.runs import RunRecord, RunStatus, TraceRecorder, UsageTotals
from energyops.agents.spec import AgentConfig, AgentSpec, ModelChoice, load_prompt
from energyops.models import Message, ModelClient, ModelError, ModelRequest
from energyops.models.providers import DEFAULT_GEMINI_MODEL
from energyops.security.permissions import Permission

__all__ = [
    "SUPERVISOR_SPEC",
    "ROUTES",
    "Supervisor",
    "SupervisorOutput",
    "SupervisorRequest",
    "SupervisorTools",
    "keyword_route",
]

RouteId = Literal[
    "analyst", "report", "investigate", "savings", "ticket", "forecast", "knowledge", "decline"
]
EVENT_ID = re.compile(r"\b[Aa](\d{1,3})\b")
DIGITS = re.compile(r"\d")


class SupervisorRequest(AgentRequest):
    question: str = Field(min_length=1, max_length=500)


class SupervisorDecision(Strict):
    route: RouteId
    reason: str = Field(min_length=1, max_length=200)


class SupervisorOutput(Strict):
    route: RouteId
    reason: str
    # answered: an agent or capability answered. declined: out of scope or not allowed.
    # needs_input: more is needed from the person. ticket_pending: a ticket now waits for approval.
    # failed: the chosen agent could not produce a checked answer.
    outcome: Literal["answered", "declined", "needs_input", "ticket_pending", "failed"]
    message: str
    child_run_id: str | None = None
    agent_id: str | None = None
    result: dict[str, Any] | None = None


SUPERVISOR_SPEC = AgentSpec(
    name="EnergyOps Supervisor",
    domain="routing",
    capabilities=("routing", "scope-check", "ticket-drafting"),
    description=(
        "Reads a question and chooses which agent or capability should handle it, or declines. "
        "It never answers a question itself."
    ),
    config=AgentConfig(
        model=ModelChoice(provider="google-gemini", name=DEFAULT_GEMINI_MODEL),
        prompt_file="supervisor/v1.md",
        prompt_version="v1",
        temperature=0.0,
        max_iterations=2,
        timeout_seconds=30.0,
        max_output_tokens=256,
    ),
)


@dataclass(frozen=True, slots=True)
class Route:
    id: str
    label: str
    permission: Permission | None
    agent_id: str | None = None  # the registry agent that handles it
    needs_event: bool = False


ROUTES: dict[str, Route] = {
    r.id: r
    for r in (
        Route("analyst", "Energy analyst", Permission.RUN_AGENTS, "energy-analyst"),
        Route("report", "Report writer", Permission.RUN_AGENTS, "report-writer"),
        Route(
            "investigate",
            "Anomaly investigator",
            Permission.RUN_AGENTS,
            "anomaly-investigator",
            needs_event=True,
        ),
        Route("savings", "Savings advisor", Permission.RUN_AGENTS, "savings-advisor"),
        Route("ticket", "Maintenance ticket", Permission.DRAFT_TICKETS, needs_event=True),
        Route("forecast", "Demand forecast", Permission.VIEW_DATA),
        Route("knowledge", "Knowledge search", Permission.VIEW_DATA),
        Route("decline", "Out of scope", None),
    )
}

DECLINE_MESSAGE = (
    "I can help with energy use, costs and savings, unusual events, reports, demand forecasts, "
    "procedures from your documents and maintenance tickets for your sites. I can't help with "
    "that request."
)
NOT_ALLOWED = "Your role doesn't allow that. Ask a manager or an administrator."
NOT_AVAILABLE = "That isn't available in this installation."


@dataclass(frozen=True, slots=True)
class EventBrief:
    id: str
    kind: str
    start: str


@dataclass
class SupervisorTools:
    """What the supervisor may do for one person, supplied by the service for that person and
    that site. The supervisor holds nothing else: no database, no other sites."""

    permissions: frozenset[Permission]
    run_agent: Callable[[str, RunInputs], Awaitable[RunRecord]]
    events: Callable[[], Awaitable[list[EventBrief]]]
    draft_ticket: Callable[[str], Awaitable[dict[str, Any]]]
    forecast: Callable[[], Awaitable[dict[str, Any]]] | None = None
    knowledge: Callable[[str], Awaitable[dict[str, Any]]] | None = None
    site: str | None = None
    notes: list[str] = field(default_factory=list)


class _State(TypedDict, total=False):
    blocked: bool
    decision: SupervisorDecision | None
    route_error: tuple[str, str]
    output: SupervisorOutput | None


def _audience(question: str) -> str:
    lowered = question.lower()
    technical = ("technician", "technical", "engineer", "maintenance team")
    return "technical" if any(word in lowered for word in technical) else "manager"


def _event_id(question: str) -> str | None:
    found = EVENT_ID.search(question)
    return f"A{int(found.group(1))}" if found else None


class Supervisor:
    def __init__(self, spec: AgentSpec, model: ModelClient, tools: SupervisorTools) -> None:
        self.spec = spec
        self._model = model
        self._tools = tools
        self._prompt = load_prompt(spec)

    async def run(self, request: SupervisorRequest) -> RunRecord:
        trace = TraceRecorder()
        usage = UsageTotals()
        attempts = 0
        config = self.spec.config
        terms = (
            self.spec.guardrails.blocklist.terms if self.spec.guardrails.blocklist.enabled else ()
        )

        async def guard(state: _State) -> _State:
            started = time.perf_counter()
            is_blocked = blocked(request.question, terms)
            trace.step("check_input", started, blocked=is_blocked)
            return {"blocked": is_blocked}

        async def route(state: _State) -> _State:
            nonlocal attempts
            messages = [
                Message("system", self._prompt.text),
                Message("user", f"Message from the person:\n{request.question}"),
            ]
            problem = ""
            last = ""
            for attempt in range(1, config.max_iterations + 1):
                attempts = attempt
                if problem:
                    messages += [
                        Message("assistant", last),
                        Message(
                            "user",
                            f"Your answer was rejected: {problem}. Answer again with only the "
                            "JSON object.",
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
                    trace.step("route_call", started, attempt=attempt, error="timeout")
                    return {"route_error": ("timeout", "no answer in time")}
                except ModelError as exc:
                    trace.step("route_call", started, attempt=attempt, error=type(exc).__name__)
                    return {"route_error": (f"model_error:{type(exc).__name__}", str(exc))}
                usage.input_tokens += response.usage.input_tokens
                usage.output_tokens += response.usage.output_tokens
                usage.estimated = usage.estimated or response.usage.estimated
                trace.step(
                    "route_call",
                    started,
                    attempt=attempt,
                    provider=response.provider,
                    model=response.model,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    reply=response.text,
                )
                started = time.perf_counter()
                last = response.text
                try:
                    decision = parse_json_reply(
                        response.text, SupervisorDecision, '{"route": "...", "reason": "..."}'
                    )
                    if DIGITS.search(decision.reason):
                        raise ReplyShapeError("the reason must not contain digits or figures")
                except ReplyShapeError as exc:
                    problem = str(exc)
                    trace.step("check_route", started, attempt=attempt, problems=[problem])
                    continue
                trace.step("check_route", started, attempt=attempt, route=decision.route)
                return {"decision": decision}
            return {"route_error": ("invalid_output", problem)}

        async def act(state: _State) -> _State:
            decision = state["decision"]
            assert decision is not None
            started = time.perf_counter()
            output = await self._act(decision, request)
            trace.step(
                "dispatch",
                started,
                route=decision.route,
                outcome=output.outcome,
                child_run_id=output.child_run_id,
            )
            return {"output": output}

        def after_guard(state: _State) -> str:
            return END if state.get("blocked") else "route"

        def after_route(state: _State) -> str:
            return "act" if state.get("decision") is not None else END

        graph = StateGraph(_State)
        graph.add_node("guard", guard)
        graph.add_node("route", route)
        graph.add_node("act", act)
        graph.add_edge(START, "guard")
        graph.add_conditional_edges("guard", after_guard, {END: END, "route": "route"})
        graph.add_conditional_edges("route", after_route, {END: END, "act": "act"})
        graph.add_edge("act", END)
        final = cast(_State, await graph.compile().ainvoke({}))

        def finish(
            status: RunStatus,
            *,
            output: SupervisorOutput | None = None,
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
                output=output.model_dump() if output else None,
                error_kind=error_kind,
                error=error,
                attempts=attempts,
                usage=usage,
                steps=trace.steps,
            )

        if final.get("blocked"):
            return finish(
                RunStatus.FAILED,
                error_kind="blocklist_input",
                error="the question contains a blocked term",
            )
        if final.get("decision") is None:
            kind, message = final.get("route_error", ("invalid_output", "no route was chosen"))
            return finish(RunStatus.FAILED, error_kind=kind, error=message)
        output = final["output"]
        assert output is not None
        # A decline or a request for more detail is a normal, successful run of the supervisor.
        # A chosen agent that could not produce a checked answer is reported as a failure.
        status = RunStatus.FAILED if output.outcome == "failed" else RunStatus.SUCCEEDED
        return finish(
            status,
            output=output,
            error_kind="agent_failed" if status is RunStatus.FAILED else None,
            error=output.message if status is RunStatus.FAILED else None,
        )

    # ------------------------------------------------------------------ acting on the route

    async def _act(
        self, decision: SupervisorDecision, request: SupervisorRequest
    ) -> SupervisorOutput:
        tools = self._tools
        route = ROUTES[decision.route]

        def out(
            outcome: Literal["answered", "declined", "needs_input", "ticket_pending", "failed"],
            message: str,
            **extra: Any,
        ) -> SupervisorOutput:
            return SupervisorOutput(
                route=decision.route,
                reason=decision.reason,
                outcome=outcome,
                message=message,
                **extra,
            )

        if route.id == "decline":
            return out("declined", DECLINE_MESSAGE)
        if route.permission is not None and route.permission not in tools.permissions:
            return out("declined", NOT_ALLOWED)
        if route.id == "forecast" and tools.forecast is None:
            return out("declined", NOT_AVAILABLE)
        if route.id == "knowledge" and tools.knowledge is None:
            return out("declined", NOT_AVAILABLE)

        event = _event_id(request.question) if route.needs_event else None
        if route.needs_event and event is None:
            events = await tools.events()
            if not events:
                return out(
                    "needs_input",
                    "No unusual events were found at this site, so there is nothing to look at.",
                )
            listed = ", ".join(f"{e.id} ({e.kind} on {e.start[:10]})" for e in events[:8])
            return out("needs_input", f"Which event do you mean? I found: {listed}.")

        if route.id == "ticket":
            assert event is not None
            try:
                ticket = await tools.draft_ticket(event)
            except ToolRefused as exc:
                return out("needs_input", str(exc))
            return out(
                "ticket_pending",
                f"I drafted a ticket for event {event}. It is waiting for a manager to approve it.",
                result=ticket,
            )
        if route.id == "forecast":
            assert tools.forecast is not None
            return out("answered", "Here is the forecast.", result=await tools.forecast())
        if route.id == "knowledge":
            assert tools.knowledge is not None
            return out(
                "answered",
                "Here are the passages from your documents that match.",
                result=await tools.knowledge(request.question),
            )

        assert route.agent_id is not None
        inputs = RunInputs(
            site=tools.site,
            question=request.question if route.id == "analyst" else None,
            audience=_audience(request.question) if route.id == "report" else None,
            anomaly_id=event,
        )
        try:
            record = await tools.run_agent(route.agent_id, inputs)
        except ToolRefused as exc:
            return out("declined", str(exc))
        if record.status is RunStatus.SUCCEEDED:
            return out(
                "answered",
                f"Answered by the {route.label.lower()}.",
                child_run_id=record.run_id,
                agent_id=route.agent_id,
                result=record.output,
            )
        return out(
            "failed",
            f"The {route.label.lower()} could not produce an answer that passed its checks "
            f"({record.error_kind}).",
            child_run_id=record.run_id,
            agent_id=route.agent_id,
        )


class ToolRefused(Exception):
    """A tool declined, with a message that is safe to show the person."""


# ------------------------------------------------------------------ the test adapter's router

_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ticket", ("ticket", "work order", "raise a job", "log a job")),
    ("forecast", ("forecast", "predict", "next week", "tomorrow", "next month")),
    ("knowledge", ("procedure", "policy", "how do i", "how to", "document", "manual", "guide")),
    ("savings", ("saving", "save", "cost", "tariff", "bill", "price", "cheaper", "expensive")),
    ("investigate", ("anomal", "unusual", "spike", "investigate", "why", "event")),
    ("report", ("report", "write up", "briefing")),
    ("analyst", ("energy", "demand", "peak", "usage", "use ", "kwh", "weekend", "summar")),
)


def keyword_route(question: str) -> str:
    """The reply the labelled test adapter gives: a plain keyword match, nothing like a model.
    It exists so the screens and the tests can run without a provider. Runs that use it are
    recorded with the provider 'test-adapter'."""
    lowered = question.lower()
    for route, words in _KEYWORDS:
        if any(word in lowered for word in words):
            reason = "The wording matches this kind of request."
            return f'{{"route": "{route}", "reason": "{reason}"}}'
    return '{"route": "decline", "reason": "The request is not about energy at the sites."}'
