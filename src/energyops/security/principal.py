"""The signed-in person, as the rest of the code sees them."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from energyops.db.session import Scope
from energyops.security.permissions import ROLE_PERMISSIONS, Forbidden, Permission


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: uuid.UUID
    org_id: uuid.UUID | None  # None only for platform staff
    email: str
    name: str
    role: str
    # The sites this person may see, or None for every site of their organisation.
    site_ids: frozenset[uuid.UUID] | None

    @property
    def permissions(self) -> frozenset[Permission]:
        return ROLE_PERMISSIONS[self.role]

    def can(self, permission: Permission) -> bool:
        return permission in self.permissions

    def require(self, permission: Permission) -> None:
        if not self.can(permission):
            raise Forbidden(permission)

    @property
    def scope(self) -> Scope:
        """The database scope for this person. Customer data is reachable only through an
        organisation scope, so platform staff, who have no organisation, get the platform scope
        and so see no customer rows."""
        if self.org_id is None:
            return Scope.platform_wide()
        return Scope.for_org(self.org_id)

    def org(self) -> uuid.UUID:
        """The organisation, for code that only makes sense inside one."""
        if self.org_id is None:
            raise Forbidden(Permission.VIEW_DATA)
        return self.org_id
