"""Facility-owned patient profiles, episodes and individual staff assignments."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tunza.extensions import db


class Patient(db.Model):
    __tablename__ = "patients"
    __table_args__ = {"implicit_returning": False}

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RegistrationIdempotency(db.Model):
    __tablename__ = "patient_registration_idempotency"
    __table_args__ = (
        UniqueConstraint("actor_id", "key", name="uq_patient_registration_actor_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    facility_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("facilities.id"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    response_cipher: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    key_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FacilityPatientProfile(db.Model):
    __tablename__ = "facility_patient_profiles"
    __table_args__ = (
        UniqueConstraint("patient_id", "facility_id", name="profile_patient_facility"),
        UniqueConstraint("facility_id", "local_reference", name="profile_facility_reference"),
        CheckConstraint("status = 'synthetic'", name="synthetic_status_only"),
        CheckConstraint("version > 0", name="version_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("patients.id", ondelete="RESTRICT", name="fk_profiles_patient_id_patients"),
        nullable=False,
        index=True,
    )
    facility_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("facilities.id", ondelete="RESTRICT", name="fk_profiles_facility_id_facilities"),
        nullable=False,
        index=True,
    )
    local_reference: Mapped[str] = mapped_column(String(200), nullable=False)
    demographics_cipher: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="synthetic")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class CommunicationPreference(db.Model):
    __tablename__ = "communication_preferences"
    __table_args__ = (
        ForeignKeyConstraint(
            ["patient_id", "facility_id"],
            ["facility_patient_profiles.patient_id", "facility_patient_profiles.facility_id"],
            name="fk_preferences_patient_facility_profiles",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("patient_id", "facility_id", name="preference_patient_facility"),
        CheckConstraint("language IN ('en', 'lg')", name="language_values"),
        CheckConstraint("privacy_flags = '{}'::jsonb", name="privacy_policy_unconfigured"),
        CheckConstraint("version > 0", name="version_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    facility_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    language: Mapped[str] = mapped_column(String(2), nullable=False)
    privacy_flags: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class PregnancyEpisode(db.Model):
    __tablename__ = "pregnancy_episodes"
    __table_args__ = (
        UniqueConstraint("id", "patient_id", name="episode_id_patient"),
        CheckConstraint("lifecycle IN ('ongoing', 'completed')", name="lifecycle_values"),
        CheckConstraint(
            "communication_state IN ('active', 'paused', 'completed', 'withdrawn')",
            name="communication_state_values",
        ),
        CheckConstraint("version > 0", name="version_positive"),
        {"implicit_returning": False},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("patients.id", ondelete="RESTRICT", name="fk_episodes_patient_id_patients"),
        nullable=False,
        index=True,
    )
    lifecycle: Mapped[str] = mapped_column(String(16), nullable=False, default="ongoing")
    communication_state: Mapped[str] = mapped_column(String(16), nullable=False, default="paused")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class EpisodeFacilityAssignment(db.Model):
    __tablename__ = "episode_facility_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["episode_id", "patient_id"],
            ["pregnancy_episodes.id", "pregnancy_episodes.patient_id"],
            name="fk_episode_assignments_episode_patient",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["patient_id", "facility_id"],
            ["facility_patient_profiles.patient_id", "facility_patient_profiles.facility_id"],
            name="fk_episode_assignments_patient_facility",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "id", "episode_id", "facility_id", name="episode_assignment_id_episode_facility"
        ),
        UniqueConstraint("id", "facility_id", name="episode_assignment_id_facility"),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="valid_interval"),
        CheckConstraint("version > 0", name="version_positive"),
        Index(
            "uq_episode_facility_assignments_current_episode",
            "episode_id",
            unique=True,
            postgresql_where=text("valid_to IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    episode_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    patient_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    facility_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class ParticipantAssignment(db.Model):
    __tablename__ = "participant_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["episode_facility_assignment_id", "facility_id"],
            ["episode_facility_assignments.id", "episode_facility_assignments.facility_id"],
            name="fk_participant_assignments_episode_facility",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["membership_id", "facility_id"],
            ["facility_memberships.id", "facility_memberships.facility_id"],
            name="fk_participant_assignments_membership_facility",
            ondelete="RESTRICT",
        ),
        CheckConstraint("duty = 'primary'", name="individual_primary_duty_only"),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="valid_interval"),
        CheckConstraint("version > 0", name="version_positive"),
        Index(
            "uq_participant_assignments_current_membership_duty",
            "episode_facility_assignment_id",
            "membership_id",
            "duty",
            unique=True,
            postgresql_where=text("valid_to IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    episode_facility_assignment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, nullable=False, index=True
    )
    facility_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    membership_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    duty: Mapped[str] = mapped_column(String(32), nullable=False, default="primary")
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class ProtectedEvidence(db.Model):
    __tablename__ = "protected_evidence"
    __table_args__ = (
        UniqueConstraint("id", "facility_id", name="uq_protected_evidence_id_facility"),
        {"implicit_returning": False},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    facility_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "facilities.id",
            ondelete="RESTRICT",
            name="fk_protected_evidence_facility_id_facilities",
        ),
        nullable=False,
        index=True,
    )
    storage_reference: Mapped[str] = mapped_column(String(500), nullable=False)
    digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    media_type: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_by: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_protected_evidence_recorded_by_users"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AdultEligibilityCheck(db.Model):
    __tablename__ = "adult_eligibility_checks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["patient_id", "facility_id"],
            ["facility_patient_profiles.patient_id", "facility_patient_profiles.facility_id"],
            name="fk_adult_eligibility_patient_facility_profiles",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["evidence_id", "facility_id"],
            ["protected_evidence.id", "protected_evidence.facility_id"],
            name="fk_adult_eligibility_evidence_protected_evidence",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "patient_id",
            "facility_id",
            "sequence",
            name="uq_adult_eligibility_patient_facility_sequence",
        ),
        CheckConstraint(
            "result IN ('verified', 'uncertain', 'ineligible')",
            name="ck_adult_eligibility_result_values",
        ),
        CheckConstraint("sequence > 0", name="ck_adult_eligibility_sequence_positive"),
        CheckConstraint("version > 0", name="ck_adult_eligibility_version_positive"),
        {"implicit_returning": False},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    facility_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    checked_by: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_adult_eligibility_checked_by_users"),
        nullable=False,
    )
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class ConsentNoticeVersion(db.Model):
    __tablename__ = "consent_notice_versions"
    __table_args__ = (
        UniqueConstraint(
            "notice_code",
            "revision",
            "language",
            name="uq_consent_notice_version_code_revision_language",
        ),
        CheckConstraint(
            "language IN ('en', 'lg')",
            name="ck_consent_notice_version_language_values",
        ),
        CheckConstraint(
            "status IN ('approved', 'rejected', 'superseded')",
            name="ck_consent_notice_version_status_values",
        ),
        CheckConstraint("version > 0", name="ck_consent_notice_version_version_positive"),
        {"implicit_returning": False},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    notice_code: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    language: Mapped[str] = mapped_column(String(2), nullable=False)
    text_cipher: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    purpose_codes: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False, server_default="{}"
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="approved")
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class ConsentEvent(db.Model):
    __tablename__ = "consent_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["patient_id", "facility_id"],
            ["facility_patient_profiles.patient_id", "facility_patient_profiles.facility_id"],
            name="fk_consent_event_patient_facility_profiles",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["episode_id", "patient_id"],
            ["pregnancy_episodes.id", "pregnancy_episodes.patient_id"],
            name="fk_consent_event_episode_patient",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["notice_version_id"],
            ["consent_notice_versions.id"],
            name="fk_consent_event_notice_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["evidence_id", "facility_id"],
            ["protected_evidence.id", "protected_evidence.facility_id"],
            name="fk_consent_event_evidence_protected_evidence",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["supersedes_id"],
            ["consent_events.id"],
            name="fk_consent_event_supersedes_consent_event",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "decision IN ('grant', 'withdraw', 'correct')",
            name="ck_consent_event_decision_values",
        ),
        CheckConstraint(
            "language IN ('en', 'lg')",
            name="ck_consent_event_language_values",
        ),
        CheckConstraint("event_sequence > 0", name="ck_consent_event_sequence_positive"),
        CheckConstraint("version > 0", name="ck_consent_event_version_positive"),
        {"implicit_returning": False},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    facility_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    episode_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    notice_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    method: Mapped[str] = mapped_column(String(64), nullable=False)
    language: Mapped[str] = mapped_column(String(2), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_by: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_consent_event_recorded_by_users"),
        nullable=False,
    )
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    event_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


__all__ = [
    "AdultEligibilityCheck",
    "CommunicationPreference",
    "ConsentEvent",
    "ConsentNoticeVersion",
    "EpisodeFacilityAssignment",
    "FacilityPatientProfile",
    "ParticipantAssignment",
    "Patient",
    "PregnancyEpisode",
    "ProtectedEvidence",
]
