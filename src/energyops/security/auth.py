"""Turning a verified token into a Principal, using our own database.

The token says who the person is. Whether they may use the product, which organisation they
belong to, their role and the sites they may see all come from the database. A person who was
never added (or was switched off, or whose organisation was) is refused however valid their
token is.
"""

from __future__ import annotations

from energyops.db import repo
from energyops.db.session import Database, Scope
from energyops.security.principal import Principal
from energyops.security.tokens import Claims


class NotProvisioned(Exception):
    """A valid token for someone who has not been given access."""


async def authenticate(db: Database, claims: Claims) -> Principal:
    # Step 1 runs before the organisation is known, so it uses the platform scope, which can read
    # people but no customer data.
    async with db.session(Scope.platform_wide()) as session:
        user = await repo.find_user(session, email=claims.email, external_id=claims.external_id)
        if user is None or not user.is_active:
            raise NotProvisioned("This account has not been given access.")
        if claims.external_id is not None and user.external_id != claims.external_id:
            if user.external_id is not None:
                # The email matches a person already linked to a different identity.
                raise NotProvisioned("This account has not been given access.")
            user.external_id = claims.external_id  # first sign-in: remember the stable id
            await session.flush()
        if user.org_id is not None:
            organisation = await repo.get_organization(session, user.org_id)
            if organisation is None or not organisation.is_active:
                raise NotProvisioned("This organisation is not active.")
        user_id, org_id, email = user.id, user.org_id, user.email
        name, role, all_sites = user.display_name, user.role, user.all_sites

    site_ids = None
    if org_id is not None and not all_sites:
        async with db.session(Scope.for_org(org_id)) as session:
            site_ids = await repo.site_ids_for(session, org_id, user_id)
    return Principal(
        user_id=user_id, org_id=org_id, email=email, name=name, role=role, site_ids=site_ids
    )
