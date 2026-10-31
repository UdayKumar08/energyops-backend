"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-28 07:51:00.295014
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Tables whose rows belong to one organisation. A session sees only its own organisation's rows.
TENANT_TABLES = ("user_site_access", "sites", "meters", "readings", "tariffs", "runs")


def _app_role() -> str:
    return os.environ.get("ENERGYOPS_DB_APP_ROLE", "energyops_app")


def _security() -> None:
    """Row level security and the application role's privileges.

    The application connects as a role that owns nothing, so these policies apply to it. The
    session sets `app.org_id` from the signed-in person (never from anything a model or tool
    produced) and, for a few audited code paths, `app.platform`.
    """
    op.execute(
        "create function app_org_id() returns uuid language sql stable as "
        "$$ select nullif(current_setting('app.org_id', true), '')::uuid $$"
    )
    op.execute(
        "create function app_is_platform() returns boolean language sql stable as "
        "$$ select coalesce(current_setting('app.platform', true), '') = 'on' $$"
    )
    for table in TENANT_TABLES:
        op.execute(f"alter table {table} enable row level security")
        op.execute(
            f"create policy tenant_isolation on {table} "
            "using (org_id = app_org_id()) with check (org_id = app_org_id())"
        )
    # Organisations: a person sees their own, the platform sees all, only the platform changes them.
    op.execute("alter table organizations enable row level security")
    op.execute(
        "create policy org_read on organizations for select "
        "using (id = app_org_id() or app_is_platform())"
    )
    op.execute(
        "create policy org_write on organizations for all "
        "using (app_is_platform()) with check (app_is_platform())"
    )
    # Users: an organisation manages its own people. Platform admins have no organisation.
    op.execute("alter table users enable row level security")
    op.execute(
        "create policy user_scope on users "
        "using (org_id = app_org_id() or app_is_platform()) "
        "with check (org_id = app_org_id() or app_is_platform())"
    )
    # The audit log: organisation rows for the organisation, every row for the platform.
    op.execute("alter table audit_events enable row level security")
    op.execute(
        "create policy audit_scope on audit_events "
        "using (org_id = app_org_id() or app_is_platform()) "
        "with check (org_id = app_org_id() or app_is_platform())"
    )
    # Agent settings are part of the product: anyone signed in may read, only the platform writes.
    op.execute("alter table agent_settings enable row level security")
    op.execute("create policy agent_settings_read on agent_settings for select using (true)")
    op.execute(
        "create policy agent_settings_write on agent_settings for all "
        "using (app_is_platform()) with check (app_is_platform())"
    )

    role = _app_role()
    op.execute(
        f"""
        do $$ begin
          if exists (select from pg_roles where rolname = '{role}') then
            execute 'grant usage on schema public to {role}';
            execute 'grant select, insert, update, delete on all tables in schema public to {role}';
            execute 'alter default privileges in schema public grant select, insert, update, delete on tables to {role}';
            execute 'grant execute on all functions in schema public to {role}';
          end if;
        end $$
        """
    )


def upgrade() -> None:
    # The pgvector extension is created by a privileged role before migrations run; this only
    # creates it when it is missing (a local database). On Azure the server refuses the CREATE
    # EXTENSION statement itself to anyone outside its administrator group, even with IF NOT
    # EXISTS and even when the extension is already there, so the statement must not run then.
    op.execute(
        """
        do $$
        begin
            if not exists (select 1 from pg_extension where extname = 'vector') then
                create extension vector;
            end if;
        end $$
        """
    )
    op.create_table(
        "agent_settings",
        sa.Column("agent_id", sa.String(length=64), nullable=False),
        sa.Column(
            "overrides",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("agent_id", name=op.f("pk_agent_settings")),
    )
    op.create_table(
        "organizations",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organizations")),
        sa.UniqueConstraint("slug", name=op.f("uq_organizations_slug")),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=True),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("actor_email", sa.String(length=320), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=True),
        sa.Column("target_id", sa.String(length=128), nullable=True),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_audit_events_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index(
        "ix_audit_events_org_created",
        "audit_events",
        ["org_id", sa.literal_column("created_at desc")],
        unique=False,
    )
    op.create_table(
        "sites",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_sites_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sites")),
        sa.UniqueConstraint("org_id", "slug", name=op.f("uq_sites_org_id")),
    )
    op.create_index(op.f("ix_sites_org_id"), "sites", ["org_id"], unique=False)
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=True),
        sa.Column("all_sites", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(role = 'platform_admin') = (org_id is null)",
            name=op.f("ck_users_platform_has_no_org"),
        ),
        sa.CheckConstraint(
            "role in ('platform_admin','org_admin','manager','technician','viewer')",
            name=op.f("ck_users_role_known"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_users_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("external_id", name=op.f("uq_users_external_id")),
    )
    op.create_index(op.f("ix_users_org_id"), "users", ["org_id"], unique=False)
    op.create_index(
        "uq_users_email_lower", "users", [sa.literal_column("lower(email)")], unique=True
    )
    op.create_table(
        "meters",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("site_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("interval_minutes", sa.Integer(), server_default=sa.text("15"), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_meters_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["site_id"], ["sites.id"], name=op.f("fk_meters_site_id_sites"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_meters")),
        sa.UniqueConstraint("site_id", "code", name=op.f("uq_meters_site_id")),
    )
    op.create_index(op.f("ix_meters_org_id"), "meters", ["org_id"], unique=False)
    op.create_index(
        "uq_meters_one_primary_per_site",
        "meters",
        ["site_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("agent_id", sa.String(length=64), nullable=False),
        sa.Column("agent_name", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=16), nullable=False),
        sa.Column("site_slug", sa.String(length=64), nullable=True),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_kind", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column(
            "tokens_estimated", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("record", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_runs_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_runs_user_id_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runs")),
    )
    op.create_index("ix_runs_org_agent", "runs", ["org_id", "agent_id"], unique=False)
    op.create_index(
        "ix_runs_org_started",
        "runs",
        ["org_id", sa.literal_column("started_at desc")],
        unique=False,
    )
    op.create_table(
        "tariffs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("site_id", sa.UUID(), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("price_per_kwh", sa.Float(), nullable=False),
        sa.Column(
            "bands",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "demand_charge_per_kw_month", sa.Float(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("is_demo", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_tariffs_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["site_id"], ["sites.id"], name=op.f("fk_tariffs_site_id_sites"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tariffs")),
        sa.UniqueConstraint("site_id", name=op.f("uq_tariffs_site_id")),
    )
    op.create_index(op.f("ix_tariffs_org_id"), "tariffs", ["org_id"], unique=False)
    op.create_table(
        "user_site_access",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("site_id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_user_site_access_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["site_id"],
            ["sites.id"],
            name=op.f("fk_user_site_access_site_id_sites"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_site_access_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "site_id", name=op.f("pk_user_site_access")),
    )
    op.create_index(
        op.f("ix_user_site_access_org_id"), "user_site_access", ["org_id"], unique=False
    )
    op.create_table(
        "readings",
        sa.Column("meter_id", sa.UUID(), nullable=False),
        sa.Column("ts", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("power_kw", sa.Float(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "power_kw between -1000000 and 1000000", name=op.f("ck_readings_power_in_range")
        ),
        sa.ForeignKeyConstraint(
            ["meter_id"],
            ["meters.id"],
            name=op.f("fk_readings_meter_id_meters"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_readings_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("meter_id", "ts", name=op.f("pk_readings")),
    )
    op.create_index(op.f("ix_readings_org_id"), "readings", ["org_id"], unique=False)
    _security()


def downgrade() -> None:
    op.drop_index(op.f("ix_readings_org_id"), table_name="readings")
    op.drop_table("readings")
    op.drop_index(op.f("ix_user_site_access_org_id"), table_name="user_site_access")
    op.drop_table("user_site_access")
    op.drop_index(op.f("ix_tariffs_org_id"), table_name="tariffs")
    op.drop_table("tariffs")
    op.drop_index("ix_runs_org_started", table_name="runs")
    op.drop_index("ix_runs_org_agent", table_name="runs")
    op.drop_table("runs")
    op.drop_index(
        "uq_meters_one_primary_per_site",
        table_name="meters",
        postgresql_where=sa.text("is_primary"),
    )
    op.drop_index(op.f("ix_meters_org_id"), table_name="meters")
    op.drop_table("meters")
    op.drop_index("uq_users_email_lower", table_name="users")
    op.drop_index(op.f("ix_users_org_id"), table_name="users")
    op.drop_table("users")
    op.drop_index(op.f("ix_sites_org_id"), table_name="sites")
    op.drop_table("sites")
    op.drop_index("ix_audit_events_org_created", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("organizations")
    op.drop_table("agent_settings")
    op.execute("drop function if exists app_org_id()")
    op.execute("drop function if exists app_is_platform()")
