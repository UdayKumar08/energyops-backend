"""The tables. Every table that holds a customer's data has an `org_id` column, and a row level
security policy (see the migrations) lets a session see only the rows of the organisation it was
opened for. The repository code filters by organisation as well, so a mistake in one layer is
caught by the other."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from energyops.db.base import Base, created_at, uuid_pk

ROLES = ("platform_admin", "org_admin", "manager", "technician", "viewer")


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = created_at()


class User(Base):
    """A person who can sign in. `external_id` is the identity provider's stable id for them
    (the Entra `oid` claim); it is filled in on their first sign-in. A person who is not in this
    table cannot use the product, however valid their token is."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role in ('platform_admin','org_admin','manager','technician','viewer')",
            name="role_known",
        ),
        CheckConstraint("(role = 'platform_admin') = (org_id is null)", name="platform_has_no_org"),
        Index("uq_users_email_lower", text("lower(email)"), unique=True),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20))
    external_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    # False means the person sees only the sites listed in user_site_access.
    all_sites: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = created_at()


class Site(Base):
    __tablename__ = "sites"
    __table_args__ = (UniqueConstraint("org_id", "slug"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    slug: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = created_at()

    meters: Mapped[list[Meter]] = relationship(back_populates="site", lazy="raise")


class UserSiteAccess(Base):
    __tablename__ = "user_site_access"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )


class Meter(Base):
    __tablename__ = "meters"
    __table_args__ = (
        UniqueConstraint("site_id", "code"),
        Index(
            "uq_meters_one_primary_per_site",
            "site_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(64))
    interval_minutes: Mapped[int] = mapped_column(Integer, server_default=text("15"))
    is_primary: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = created_at()

    site: Mapped[Site] = relationship(back_populates="meters", lazy="raise")


class Reading(Base):
    """Average power over one interval starting at `ts` (UTC). Power in kW, never energy."""

    __tablename__ = "readings"
    # NaN sorts above every number in Postgres, so this also refuses NaN and infinity.
    __table_args__ = (
        CheckConstraint("power_kw between -1000000 and 1000000", name="power_in_range"),
    )

    meter_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meters.id", ondelete="CASCADE"), primary_key=True
    )
    ts: Mapped[datetime] = mapped_column(primary_key=True)
    power_kw: Mapped[float] = mapped_column(Float)
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )


class Tariff(Base):
    __tablename__ = "tariffs"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sites.id", ondelete="CASCADE"), unique=True
    )
    label: Mapped[str] = mapped_column(String(200))
    currency: Mapped[str] = mapped_column(String(3))
    price_per_kwh: Mapped[float] = mapped_column(Float)
    # A list of {"name", "start_hour", "end_hour", "price_per_kwh", "days"}.
    bands: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    demand_charge_per_kw_month: Mapped[float] = mapped_column(Float, server_default=text("0"))
    is_demo: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class AgentSetting(Base):
    """What an administrator has changed about an agent. The code holds the defaults (the
    AgentSpec); this row holds validated overrides, so a change is visible, dated and reversible.
    Not tied to an organisation: agents are part of the product."""

    __tablename__ = "agent_settings"

    agent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    overrides: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    updated_by: Mapped[uuid.UUID | None] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Run(Base):
    """One finished agent run. `record` is the whole RunRecord, trace included; the other columns
    are copies of its main fields so lists and usage totals do not have to open the JSON."""

    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_org_started", "org_id", text("started_at desc")),
        Index("ix_runs_org_agent", "org_id", "agent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    agent_id: Mapped[str] = mapped_column(String(64))
    agent_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(16))
    site_slug: Mapped[str | None] = mapped_column(String(64))
    input: Mapped[dict[str, Any]] = mapped_column(JSONB)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_kind: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer)
    input_tokens: Mapped[int] = mapped_column(BigInteger)
    output_tokens: Mapped[int] = mapped_column(BigInteger)
    tokens_estimated: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime]
    record: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at()


class AuditEvent(Base):
    """Who did what. Rows are only ever added. `org_id` is empty for platform level actions."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_org_created", "org_id", text("created_at desc")),)

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column()
    actor_email: Mapped[str | None] = mapped_column(String(320))
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(128))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = created_at()


# Tables whose rows belong to one organisation and are protected by row level security.
TENANT_TABLES = (
    "user_site_access",
    "sites",
    "meters",
    "readings",
    "tariffs",
    "runs",
    "tickets",
    "ingestions",
    "documents",
    "chunks",
)


class Ticket(Base):
    """A maintenance ticket drafted from an event and waiting for, or given, a person's decision.

    Nothing is sent to an outside ticketing system: this table is the whole record. `id` is also
    the id of the approval graph's saved state, so a ticket can only be resumed by someone who
    could first read this row (which row level security and the site filter control)."""

    __tablename__ = "tickets"
    __table_args__ = (
        CheckConstraint("priority in ('low', 'medium', 'high')", name="priority"),
        CheckConstraint("status in ('pending_approval', 'approved', 'rejected')", name="status"),
        Index("ix_tickets_org_status", "org_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    anomaly_id: Mapped[str | None] = mapped_column(String(8))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(8))
    evidence: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    status: Mapped[str] = mapped_column(String(20))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at()
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column()
    decision_note: Mapped[str | None] = mapped_column(Text)


class Ingestion(Base):
    """A record that readings were uploaded: who, which meter, what the file held. The file
    itself is not kept; the readings it held are in `readings`."""

    __tablename__ = "ingestions"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    meter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meters.id", ondelete="CASCADE"))
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    filename: Mapped[str | None] = mapped_column(String(200))
    sha256: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int] = mapped_column(Integer)
    row_count: Mapped[int] = mapped_column(Integer)
    first_ts: Mapped[datetime] = mapped_column()
    last_ts: Mapped[datetime] = mapped_column()
    created_at: Mapped[datetime] = created_at()


class Document(Base):
    """A text document an organisation uploaded for knowledge search. Organisation-wide: it is
    not tied to a site, so site restrictions do not apply to it."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    filename: Mapped[str | None] = mapped_column(String(200))
    byte_size: Mapped[int] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer)
    embedder: Mapped[str] = mapped_column(String(120))
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at()


class Chunk(Base):
    """One passage of a document with its embedding. `embedder` names what produced the vector:
    vectors from different embedders cannot be compared, so a search only looks at the passages
    made by the embedder in use."""

    __tablename__ = "chunks"
    __table_args__ = (UniqueConstraint("document_id", "position"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedder: Mapped[str] = mapped_column(String(120))
    embedding: Mapped[list[float]] = mapped_column(Vector(768))
