"""Durable candidates, redacted operations projections and synthetic capacity tests."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tunza.extensions import db

REASON_CODES = (
    '["D05_unconfigured","D06_unconfigured","D07_unconfigured","D08_unconfigured",'
    '"D14_unconfigured","D14a_unconfigured","D15_unconfigured",'
    '"contact_safety_unconfigured","consent_policy_unconfigured","policy_missing",'
    '"global_paused","facility_paused","budget_missing","language_unavailable",'
    '"consent_absent","consent_withdrawn","consent_unapproved",'
    '"adult_eligibility_unverified","adult_eligibility_unapproved","episode_completed",'
    '"communication_paused","communication_completed","communication_withdrawn",'
    '"assignment_changed","dating_missing","appointment_changed","appointment_expired",'
    '"content_fixture_only","content_unavailable","expired"]'
)


class Identity:
    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class MessageOccurrence(Identity, db.Model):
    __tablename__ = "message_occurrences"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["episode_id", "patient_id"],
            ["pregnancy_episodes.id", "pregnancy_episodes.patient_id"],
            name="fk_occurrence_episode",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assignment_id", "episode_id", "facility_id"],
            [
                "episode_facility_assignments.id",
                "episode_facility_assignments.episode_id",
                "episode_facility_assignments.facility_id",
            ],
            name="fk_occurrence_assignment",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["appointment_id", "episode_id", "patient_id", "facility_id"],
            [
                "appointments.id",
                "appointments.episode_id",
                "appointments.patient_id",
                "appointments.facility_id",
            ],
            name="fk_occurrence_appointment",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("programme = 'antenatal'", name="occurrence_programme"),
        sa.CheckConstraint(
            "(kind = 'weekly' AND gestational_week >= 0 AND "
            "gestational_week IS NOT NULL AND appointment_id IS NULL AND "
            "reminder_code IS NULL) OR (kind = 'appointment_reminder' AND "
            "gestational_week IS NULL AND appointment_id IS NOT NULL AND "
            "reminder_code ~ '^SYN-[A-Z0-9-]{2,60}$')",
            name="occurrence_business_key",
        ),
        sa.CheckConstraint(
            "state IN ('held','suppressed','cancelled','expired')", name="occurrence_no_activation"
        ),
        sa.CheckConstraint("language IN ('en','lg')", name="occurrence_language"),
        sa.CheckConstraint("expires_at > due_at", name="occurrence_window"),
        sa.CheckConstraint(
            "version > 0 AND jsonb_typeof(hold_reasons) = 'array' "
            "AND jsonb_array_length(hold_reasons) > 0",
            name="occurrence_holds",
        ),
        sa.CheckConstraint(
            f"hold_reasons <@ '{REASON_CODES}'::jsonb AND hold_reasons ? 'D07_unconfigured'",
            name="occurrence_reason_codes",
        ),
        sa.Index(
            "uq_occurrence_weekly",
            "episode_id",
            "programme",
            "gestational_week",
            unique=True,
            postgresql_where=sa.text("kind = 'weekly'"),
        ),
        sa.Index(
            "uq_occurrence_reminder",
            "appointment_id",
            "reminder_code",
            unique=True,
            postgresql_where=sa.text("kind = 'appointment_reminder'"),
        ),
        sa.Index("ix_occurrence_due", "facility_id", "state", "due_at"),
    )
    episode_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    patient_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    facility_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    assignment_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    programme: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    kind: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    gestational_week: Mapped[int | None] = mapped_column(sa.Integer)
    appointment_id: Mapped[UUID | None] = mapped_column(sa.Uuid)
    reminder_code: Mapped[str | None] = mapped_column(sa.String(64))
    template_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("message_templates.id", ondelete="RESTRICT")
    )
    language: Mapped[str] = mapped_column(sa.String(2), nullable=False)
    due_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    hold_reasons: Mapped[list] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


class MessageDispatch(Identity, db.Model):
    __tablename__ = "message_dispatches"
    __table_args__ = (
        sa.CheckConstraint(
            "state IN ('held','suppressed','cancelled','expired')", name="dispatch_no_submission"
        ),
        sa.CheckConstraint(
            "(template_version_id IS NULL) = (publication_version IS NULL) "
            "AND (publication_version IS NULL OR publication_version > 0)",
            name="dispatch_fixture_pair",
        ),
    )
    occurrence_id: Mapped[UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("message_occurrences.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    state: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    template_version_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("template_versions.id", ondelete="RESTRICT")
    )
    publication_version: Mapped[int | None] = mapped_column(sa.Integer)


class MessageSchedulingEvent(Identity, db.Model):
    __tablename__ = "message_scheduling_events"
    __table_args__ = (
        sa.UniqueConstraint("occurrence_id", "sequence", name="uq_scheduling_event_sequence"),
    )
    occurrence_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("message_occurrences.id", ondelete="RESTRICT"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    state: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    hold_reasons: Mapped[list] = mapped_column(JSONB, nullable=False)
    audit_event_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("audit_events.id", ondelete="RESTRICT"), nullable=False, unique=True
    )


class MessageOperation(Identity, db.Model):
    """Identity-free projection: operations never SELECT the occurrence table."""

    __tablename__ = "message_operations"
    facility_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("facilities.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    state: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    hold_reasons: Mapped[list] = mapped_column(JSONB, nullable=False)
    due_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)


class MessageOperationEvent(Identity, db.Model):
    __tablename__ = "message_operation_events"
    __table_args__ = (
        sa.UniqueConstraint("occurrence_id", "sequence", name="uq_operation_event_sequence"),
    )
    occurrence_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("message_operations.id", ondelete="RESTRICT"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    state: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    hold_reasons: Mapped[list] = mapped_column(JSONB, nullable=False)


class MessagingPolicy(Identity, db.Model):
    __tablename__ = "messaging_policies"
    __table_args__ = (
        sa.CheckConstraint(
            "synthetic_only = true AND config = '{}'::jsonb", name="messaging_policy_fixture"
        ),
        sa.CheckConstraint(
            "code ~ '^SYN-[A-Z0-9-]{2,60}$' AND revision > 0", name="messaging_policy_code"
        ),
        sa.Index(
            "uq_messaging_global_revision",
            "revision",
            unique=True,
            postgresql_where=sa.text("facility_id IS NULL"),
        ),
        sa.Index(
            "uq_messaging_facility_revision",
            "facility_id",
            "revision",
            unique=True,
            postgresql_where=sa.text("facility_id IS NOT NULL"),
        ),
    )
    facility_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("facilities.id", ondelete="RESTRICT")
    )
    code: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    revision: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    paused: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    synthetic_only: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)


class MessagingBudget(Identity, db.Model):
    __tablename__ = "messaging_budgets"
    __table_args__ = (
        sa.CheckConstraint(
            "synthetic_only = true AND currency = 'SYN'", name="messaging_budget_fixture"
        ),
        sa.CheckConstraint(
            "period_end > period_start AND cap >= 0 AND unit_cost > 0",
            name="messaging_budget_values",
        ),
    )
    facility_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("facilities.id", ondelete="RESTRICT")
    )
    period_start: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False)
    cap: Mapped[Decimal] = mapped_column(sa.Numeric(14, 4), nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(sa.Numeric(14, 4), nullable=False)
    synthetic_only: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)


class CostReservation(Identity, db.Model):
    """Capacity simulations have no dispatch/attempt link and cannot authorize work."""

    __tablename__ = "cost_reservations"
    __table_args__ = (
        sa.UniqueConstraint("budget_id", "actor_id", "key", name="uq_capacity_reservation"),
        sa.CheckConstraint(
            "synthetic_only = true AND segments > 0 AND amount > 0",
            name="capacity_reservation_values",
        ),
    )
    budget_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("messaging_budgets.id", ondelete="RESTRICT"), nullable=False
    )
    audit_event_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("audit_events.id", ondelete="RESTRICT"), nullable=False
    )
    facility_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("facilities.id", ondelete="RESTRICT"), nullable=False
    )
    actor_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    segments: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    amount: Mapped[Decimal] = mapped_column(sa.Numeric(14, 4), nullable=False)
    synthetic_only: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)


class MessageCommand(Identity, db.Model):
    __tablename__ = "message_commands"
    __table_args__ = (
        sa.UniqueConstraint("actor_id", "operation", "key", name="uq_message_command"),
    )
    actor_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    facility_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("facilities.id", ondelete="RESTRICT")
    )
    episode_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("pregnancy_episodes.id", ondelete="RESTRICT")
    )
    operation: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    request_hash: Mapped[bytes] = mapped_column(sa.LargeBinary, nullable=False)
    response_cipher: Mapped[bytes] = mapped_column(sa.LargeBinary, nullable=False)
    key_version: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(sa.Integer, nullable=False)
