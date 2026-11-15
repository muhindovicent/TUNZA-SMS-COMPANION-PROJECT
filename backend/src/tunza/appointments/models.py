"""Facility-owned appointments, immutable events and durable command replay."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from tunza.extensions import db

STATES = ("scheduled", "confirmed", "attended", "missed", "cancelled", "rescheduled")


class Appointment(db.Model):
    __tablename__ = "appointments"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["episode_id", "patient_id"],
            ["pregnancy_episodes.id", "pregnancy_episodes.patient_id"],
            name="fk_appointments_episode_patient",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["episode_facility_assignment_id", "episode_id", "facility_id"],
            [
                "episode_facility_assignments.id",
                "episode_facility_assignments.episode_id",
                "episode_facility_assignments.facility_id",
            ],
            name="fk_appointments_responsibility",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "id", "episode_id", "patient_id", "facility_id", name="uq_appointments_scope"
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_appointment_id", "episode_id", "patient_id", "facility_id"],
            [
                "appointments.id",
                "appointments.episode_id",
                "appointments.patient_id",
                "appointments.facility_id",
            ],
            name="fk_appointments_predecessor_scope",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("supersedes_appointment_id", name="uq_appointments_one_replacement"),
        sa.CheckConstraint(
            "status IN ('scheduled','confirmed','attended','missed','cancelled','rescheduled')",
            name="appointment_status",
        ),
        sa.CheckConstraint("version > 0", name="appointment_version"),
        sa.CheckConstraint(
            "(confirmed_by IS NULL) = (confirmed_at IS NULL)", name="confirmation_pair"
        ),
        sa.CheckConstraint(
            "status NOT IN ('confirmed','attended','missed') OR confirmed_by IS NOT NULL",
            name="confirmation_required",
        ),
        sa.CheckConstraint(
            "(status IN ('attended','missed')) = (attendance_effective_at IS NOT NULL)",
            name="attendance_required",
        ),
        sa.Index("ix_appointments_facility_time_status", "facility_id", "starts_at", "status"),
        sa.Index(
            "uq_appointments_active_slot",
            "episode_facility_assignment_id",
            "starts_at",
            unique=True,
            postgresql_where=sa.text("status IN ('scheduled','confirmed')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid4)
    episode_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False, index=True)
    patient_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    facility_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    episode_facility_assignment_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="scheduled")
    created_by: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="RESTRICT")
    )
    confirmed_by: Mapped[UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="RESTRICT")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    attendance_effective_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    supersedes_appointment_id: Mapped[UUID | None] = mapped_column(sa.Uuid)
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="1", default=1)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )


class AppointmentEvent(db.Model):
    __tablename__ = "appointment_events"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["completion_record_id", "episode_id", "patient_id", "facility_id"],
            [
                "pregnancy_completion_records.id",
                "pregnancy_completion_records.episode_id",
                "pregnancy_completion_records.patient_id",
                "pregnancy_completion_records.facility_id",
            ],
            name="fk_appointment_event_completion_scope",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "completion_record_id IS NULL OR event_type = 'cancelled'",
            name="completion_event_cancelled",
        ),
        sa.ForeignKeyConstraint(
            ["appointment_id", "episode_id", "patient_id", "facility_id"],
            [
                "appointments.id",
                "appointments.episode_id",
                "appointments.patient_id",
                "appointments.facility_id",
            ],
            name="fk_appointment_events_scope",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("appointment_id", "sequence", name="uq_appointment_event_sequence"),
        sa.CheckConstraint("sequence > 0", name="event_sequence"),
        sa.CheckConstraint(
            "event_type IN ('scheduled','confirmed','attended','missed','cancelled','rescheduled')",
            name="event_type",
        ),
    )
    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid4)
    appointment_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False, index=True)
    completion_record_id: Mapped[UUID | None] = mapped_column(sa.Uuid)
    episode_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    patient_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    facility_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    event_type: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    sequence: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    event_data_cipher: Mapped[bytes] = mapped_column(sa.LargeBinary, nullable=False)
    key_version: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    recorded_by: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    effective_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class AppointmentIdempotency(db.Model):
    __tablename__ = "appointment_idempotency"
    __table_args__ = (
        sa.UniqueConstraint("actor_id", "operation", "key", name="uq_appointment_command_key"),
        sa.ForeignKeyConstraint(
            ["appointment_id", "episode_id", "patient_id", "facility_id"],
            [
                "appointments.id",
                "appointments.episode_id",
                "appointments.patient_id",
                "appointments.facility_id",
            ],
            name="fk_appointment_command_scope",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("response_status IN (200,201)", name="command_response_status"),
    )
    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid4)
    actor_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    operation: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    appointment_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    episode_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    patient_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    facility_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    request_hash: Mapped[bytes] = mapped_column(sa.LargeBinary, nullable=False)
    response_cipher: Mapped[bytes] = mapped_column(sa.LargeBinary, nullable=False)
    key_version: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
