"""tickets

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-28 08:20:10.227264
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tickets",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("site_id", sa.UUID(), nullable=False),
        sa.Column("anomaly_id", sa.String(length=8), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(length=8), nullable=False),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("decided_by", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "priority in ('low', 'medium', 'high')", name=op.f("ck_tickets_priority")
        ),
        sa.CheckConstraint(
            "status in ('pending_approval', 'approved', 'rejected')", name=op.f("ck_tickets_status")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_tickets_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["users.id"],
            name=op.f("fk_tickets_decided_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_tickets_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["site_id"], ["sites.id"], name=op.f("fk_tickets_site_id_sites"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tickets")),
    )
    op.create_index(op.f("ix_tickets_org_id"), "tickets", ["org_id"], unique=False)
    op.create_index("ix_tickets_org_status", "tickets", ["org_id", "status"], unique=False)
    # Like every table of customer data, a ticket is visible only inside its organisation.
    op.execute("alter table tickets enable row level security")
    op.execute(
        "create policy tenant_isolation on tickets "
        "using (org_id = app_org_id()) with check (org_id = app_org_id())"
    )
    role = os.environ.get("ENERGYOPS_DB_APP_ROLE", "energyops_app")
    op.execute(
        f"""
        do $$ begin
          if exists (select from pg_roles where rolname = '{role}') then
            execute 'grant select, insert, update, delete on tickets to {role}';
          end if;
        end $$
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists tenant_isolation on tickets")
    op.drop_index("ix_tickets_org_status", table_name="tickets")
    op.drop_index(op.f("ix_tickets_org_id"), table_name="tickets")
    op.drop_table("tickets")
