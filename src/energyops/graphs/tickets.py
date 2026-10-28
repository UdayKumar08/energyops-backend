"""The ticket approval flow, as a LangGraph graph that stops and waits for a person.

    draft  ->  wait for a decision (the graph pauses here)  ->  apply the decision

The pause is LangGraph's `interrupt`. The graph's state is saved by a checkpointer (PostgreSQL in
the product), so the wait survives a restart and any server can resume it. Resuming needs the
ticket's thread id, which is the ticket's own id, and the API only looks that up for someone who
could first read the ticket row, so the saved state is reached only through a checked path. The
checkpoint tables themselves are not under row level security.

The graph decides nothing. It carries a draft to a person and returns what the person decided. The
API records the decision in the tickets table and the audit log, and sends nothing to any outside
system.
"""

from __future__ import annotations

import uuid
from typing import Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from energyops.mcp_servers.schema import AnomalyModel
from energyops.tickets import PRIORITIES, draft_from_event


class ApprovalState(TypedDict, total=False):
    site_name: str
    event: dict[str, Any]
    draft: dict[str, Any]
    decision: dict[str, Any]
    outcome: dict[str, Any]


class InvalidDecision(ValueError):
    """The decision handed to the graph is not one it accepts."""


def _draft(state: ApprovalState) -> ApprovalState:
    event = AnomalyModel.model_validate(state["event"])
    draft = draft_from_event(state["site_name"], event)
    return {
        "draft": {
            "title": draft.title,
            "description": draft.description,
            "priority": draft.priority,
            "evidence": list(draft.evidence),
        }
    }


def _wait(state: ApprovalState) -> ApprovalState:
    # The graph stops here. On resume the value passed in becomes this call's return value.
    decision = interrupt({"kind": "ticket_approval", "draft": state["draft"]})
    return {"decision": decision}


def _apply(state: ApprovalState) -> ApprovalState:
    decision = state["decision"]
    if not isinstance(decision.get("approve"), bool):
        raise InvalidDecision("a decision must say approve: true or false")
    priority = decision.get("priority") or state["draft"]["priority"]
    if priority not in PRIORITIES:
        raise InvalidDecision(f"priority must be one of {', '.join(PRIORITIES)}")
    return {
        "outcome": {
            "approved": decision["approve"],
            "priority": priority,
            "note": decision.get("note"),
            "decided_by": decision.get("decided_by"),
        }
    }


def build_approval_graph(checkpointer: BaseCheckpointSaver[Any]) -> Any:
    graph = StateGraph(ApprovalState)
    graph.add_node("draft", _draft)
    graph.add_node("wait_for_decision", _wait)
    graph.add_node("apply_decision", _apply)
    graph.add_edge(START, "draft")
    graph.add_edge("draft", "wait_for_decision")
    graph.add_edge("wait_for_decision", "apply_decision")
    graph.add_edge("apply_decision", END)
    return graph.compile(checkpointer=checkpointer)


def _config(ticket_id: uuid.UUID) -> dict[str, Any]:
    return {"configurable": {"thread_id": str(ticket_id)}}


async def start(
    graph: Any, ticket_id: uuid.UUID, site_name: str, event: AnomalyModel
) -> dict[str, Any]:
    """Run the graph until it pauses for a decision, and return the draft it prepared."""
    await graph.ainvoke({"site_name": site_name, "event": event.model_dump()}, _config(ticket_id))
    snapshot = await graph.aget_state(_config(ticket_id))
    if snapshot.next != ("wait_for_decision",):
        raise RuntimeError("the approval graph did not pause for a decision")
    draft: dict[str, Any] = snapshot.values["draft"]
    return draft


async def is_waiting(graph: Any, ticket_id: uuid.UUID) -> bool:
    snapshot = await graph.aget_state(_config(ticket_id))
    return bool(snapshot.next == ("wait_for_decision",))


async def decide(
    graph: Any,
    ticket_id: uuid.UUID,
    *,
    approve: bool,
    decided_by: uuid.UUID,
    note: str | None,
    priority: str | None,
) -> dict[str, Any]:
    """Resume a paused graph with a person's decision and return the outcome it computed."""
    payload = {
        "approve": approve,
        "decided_by": str(decided_by),
        "note": note,
        "priority": priority,
    }
    result = await graph.ainvoke(Command(resume=payload), _config(ticket_id))
    outcome: dict[str, Any] = result["outcome"]
    return outcome
