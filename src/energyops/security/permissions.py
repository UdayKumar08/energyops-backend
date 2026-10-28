"""What each role may do. The roles are fixed in the database; the permissions they carry are
fixed here, in code that is reviewed and tested, so no one can grant themselves a new one.

A platform admin runs the product: organisations, the agent registry, usage and the platform audit
log. They hold none of the customer permissions, so they cannot read a customer's data even
though they can create the customer's account.
"""

from __future__ import annotations

from enum import StrEnum


class Permission(StrEnum):
    VIEW_DATA = "view_data"  # sites, readings summaries, events, costs, reports already made
    VIEW_RUNS = "view_runs"
    RUN_AGENTS = "run_agents"
    ASK_ASSISTANT = "ask_assistant"
    DRAFT_TICKETS = "draft_tickets"
    APPROVE_TICKETS = "approve_tickets"
    UPLOAD_DATA = "upload_data"
    MANAGE_TARIFFS = "manage_tariffs"
    MANAGE_KNOWLEDGE = "manage_knowledge"
    MANAGE_SITES = "manage_sites"
    MANAGE_USERS = "manage_users"
    VIEW_ORG_AUDIT = "view_org_audit"
    ADMIN_PLATFORM = "admin_platform"


_VIEWER = frozenset({Permission.VIEW_DATA, Permission.VIEW_RUNS})
_TECHNICIAN = _VIEWER | {
    Permission.RUN_AGENTS,
    Permission.ASK_ASSISTANT,
    Permission.DRAFT_TICKETS,
}
_MANAGER = _TECHNICIAN | {
    Permission.APPROVE_TICKETS,
    Permission.UPLOAD_DATA,
    Permission.MANAGE_TARIFFS,
    Permission.MANAGE_KNOWLEDGE,
}
_ORG_ADMIN = _MANAGER | {
    Permission.MANAGE_SITES,
    Permission.MANAGE_USERS,
    Permission.VIEW_ORG_AUDIT,
}

ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    "viewer": _VIEWER,
    "technician": _TECHNICIAN,
    "manager": _MANAGER,
    "org_admin": _ORG_ADMIN,
    "platform_admin": frozenset({Permission.ADMIN_PLATFORM}),
}


class Forbidden(Exception):
    """The person's role does not allow this."""

    def __init__(self, permission: Permission) -> None:
        super().__init__(f"Your role does not allow this ({permission.value}).")
        self.permission = permission
