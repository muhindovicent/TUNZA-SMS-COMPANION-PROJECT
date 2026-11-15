"""Bounded transfer requests and separately owned immutable decisions/handover."""

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from tunza.extensions import db


class FacilityTransfer(db.Model):
    __tablename__ = "facility_transfers"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["source_assignment_id", "episode_id", "source_facility_id"],
            [
                "episode_facility_assignments.id",
                "episode_facility_assignments.episode_id",
                "episode_facility_assignments.facility_id",
            ],
            name="fk_transfer_source_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["episode_id", "patient_id"],
            ["pregnancy_episodes.id", "pregnancy_episodes.patient_id"],
            name="fk_transfer_episode_patient",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "source_facility_id <> destination_facility_id", name="transfer_distinct_facilities"
        ),
        sa.CheckConstraint(
            "state IN ('pending','accepted','rejected','cancelled')", name="transfer_state"
        ),
        sa.CheckConstraint("synthetic = true", name="transfer_synthetic_only"),
        sa.CheckConstraint("version > 0", name="transfer_version"),
        sa.Index(
            "uq_transfer_pending_source",
            "source_assignment_id",
            unique=True,
            postgresql_where=sa.text("state = 'pending'"),
        ),
        sa.Index("ix_transfers_destination_state", "destination_facility_id", "state"),
    )
    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid4)
    source_assignment_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    episode_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    patient_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    source_facility_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    destination_facility_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("facilities.id", ondelete="RESTRICT"), nullable=False
    )
    requested_by: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, default="pending", server_default="pending"
    )
    synthetic: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=True, server_default=sa.true()
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class TransferHandover(db.Model):
    __tablename__ = "transfer_handover_records"
    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid4)
    transfer_id: Mapped[UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("facility_transfers.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    prepared_by: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    handover_cipher: Mapped[bytes] = mapped_column(sa.LargeBinary, nullable=False)
    key_version: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class TransferDecision(db.Model):
    __tablename__ = "transfer_decisions"
    __table_args__ = (
        sa.CheckConstraint(
            "outcome IN ('accepted','rejected','cancelled')", name="transfer_decision_outcome"
        ),
        sa.CheckConstraint(
            "(outcome = 'accepted') = (destination_assignment_id IS NOT NULL "
            "AND receiver_membership_id IS NOT NULL AND destination_reference IS NOT NULL)",
            name="transfer_acceptance_receipt",
        ),
    )
    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid4)
    transfer_id: Mapped[UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("facility_transfers.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    facility_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("facilities.id", ondelete="RESTRICT"), nullable=False
    )
    recorded_by: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    outcome: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    reason_code: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    destination_assignment_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("episode_facility_assignments.id", ondelete="RESTRICT")
    )
    receiver_membership_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("facility_memberships.id", ondelete="RESTRICT")
    )
    destination_reference: Mapped[str | None] = mapped_column(sa.String(200))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class TransferIdempotency(db.Model):
    __tablename__ = "transfer_idempotency"
    __table_args__ = (
        sa.UniqueConstraint("actor_id", "operation", "key", name="uq_transfer_command_key"),
    )
    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid4)
    actor_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    facility_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("facilities.id", ondelete="RESTRICT"), nullable=False
    )
    transfer_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("facility_transfers.id", ondelete="RESTRICT"), nullable=False
    )
    operation: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    request_hash: Mapped[bytes] = mapped_column(sa.LargeBinary, nullable=False)
    response_cipher: Mapped[bytes] = mapped_column(sa.LargeBinary, nullable=False)
    key_version: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
