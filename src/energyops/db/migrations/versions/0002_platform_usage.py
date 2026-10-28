"""usage totals for the platform

Platform staff cannot read customer rows, including runs. They still need to see how much the
product is used and what it costs, so this function returns counts and token totals per
organisation, agent and day, and nothing else: no inputs, outputs, site names or people. It runs
with its owner's rights, so it checks the platform setting itself and returns nothing to anyone
else.

Revision ID: 0002
Revises: 0001
"""

import os
from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    role = os.environ.get("ENERGYOPS_DB_APP_ROLE", "energyops_app")
    op.execute(
        """
        create function platform_usage(since timestamptz)
        returns table (
            org_id uuid, agent_id text, day date, runs bigint, failed bigint,
            input_tokens numeric, output_tokens numeric
        )
        language sql stable security definer set search_path = public, pg_temp as $$
            select r.org_id, r.agent_id::text, (r.started_at at time zone 'UTC')::date,
                   count(*), count(*) filter (where r.status = 'failed'),
                   coalesce(sum(r.input_tokens), 0), coalesce(sum(r.output_tokens), 0)
            from runs r
            where app_is_platform() and r.started_at >= since
            group by 1, 2, 3
        $$
        """
    )
    op.execute("revoke all on function platform_usage(timestamptz) from public")
    op.execute(
        f"""
        do $$ begin
          if exists (select from pg_roles where rolname = '{role}') then
            execute 'grant execute on function platform_usage(timestamptz) to {role}';
          end if;
        end $$
        """
    )


def downgrade() -> None:
    op.execute("drop function if exists platform_usage(timestamptz)")
