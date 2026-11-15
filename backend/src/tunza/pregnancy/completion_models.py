"""Immutable synthetic completion revisions and encrypted durable replay."""

from datetime import date, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from tunza.extensions import db


class PregnancyCompletionRecord(db.Model):
    __tablename__ = "pregnancy_completion_records"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["episode_id", "patient_id"],
            ["pregnancy_episodes.id", "pregnancy_episodes.patient_id"],
            name="fk_completion_episode_patient",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["episode_facility_assignment_id", "episode_id", "facility_id"],
            [
                "episode_facility_assignments.id",
                "episode_facility_assignments.episode_id",
                "episode_facility_assignments.facility_id",
            ],
            name="fk_completion_responsibility",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("id", "episode_id", name="uq_completion_id_episode"),
        sa.UniqueConstraint(
            "id", "episode_id", "patient_id", "facility_id", name="uq_completion_scope"
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id", "episode_id", "patient_id", "facility_id"],
            [
                "pregnancy_completion_records.id",
                "pregnancy_completion_records.episode_id",
                "pregnancy_completion_records.patient_id",
                "pregnancy_completion_records.facility_id",
            ],
            name="fk_completion_predecessor_scope",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("supersedes_id", name="uq_completion_one_successor"),
        sa.UniqueConstraint("episode_id", "sequence", name="uq_completion_episode_sequence"),
        sa.UniqueConstraint("recorded_by", "idempotency_key", name="uq_completion_actor_key"),
        sa.Index(
            "uq_completion_initial_episode",
            "episode_id",
            unique=True,
            postgresql_where=sa.text("supersedes_id IS NULL"),
        ),
        sa.CheckConstraint("sequence > 0 AND parent_version > 1", name="completion_versions"),
        sa.CheckConstraint("cancelled_appointment_count >= 0", name="completion_cancel_count"),
    )
    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid4)
    episode_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    patient_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    facility_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    episode_facility_assignment_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    completed_on: Mapped[date] = mapped_column(sa.Date, nullable=False)
    approved_fields_cipher: Mapped[bytes] = mapped_column(sa.LargeBinary, nullable=False)
    reason_cipher: Mapped[bytes] = mapped_column(sa.LargeBinary, nullable=False)
    response_cipher: Mapped[bytes] = mapped_column(sa.LargeBinary, nullable=False)
    key_version: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    recorded_by: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    supersedes_id: Mapped[UUID | None] = mapped_column(sa.Uuid)
    sequence: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    parent_version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    cancelled_appointment_count: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    request_hash: Mapped[bytes] = mapped_column(sa.LargeBinary, nullable=False)
