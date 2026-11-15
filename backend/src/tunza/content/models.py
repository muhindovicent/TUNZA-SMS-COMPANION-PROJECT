"""Encrypted content versions, independent fixture reviews and immutable history."""

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tunza.extensions import db


class MessageTemplate(db.Model):
    __tablename__ = "message_templates"
    __table_args__ = (
        sa.CheckConstraint("programme = 'antenatal'", name="template_programme"),
        sa.CheckConstraint("code ~ '^SYN-[A-Z0-9][A-Z0-9_-]{2,59}$'", name="template_fixture_code"),
        sa.CheckConstraint(
            "(purpose = 'weekly_advice' AND trigger_kind = 'weekly' "
            "AND gestational_week IS NOT NULL AND gestational_week >= 0) "
            "OR (purpose = 'appointment_reminder' AND trigger_kind = 'appointment_reminder' "
            "AND gestational_week IS NULL)",
            name="template_trigger",
        ),
        sa.CheckConstraint("synthetic_only = true", name="template_fixture_only"),
        sa.CheckConstraint("version > 0", name="template_version"),
    )
    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(sa.String(64), nullable=False, unique=True)
    programme: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    purpose: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    trigger_kind: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    gestational_week: Mapped[int | None] = mapped_column(sa.Integer)
    created_by: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    synthetic_only: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.true(), default=True
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="1", default=1)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class TemplateVersion(db.Model):
    __tablename__ = "template_versions"
    __table_args__ = (
        sa.UniqueConstraint(
            "template_id", "language", "revision", name="uq_template_language_revision"
        ),
        sa.CheckConstraint("language IN ('en','lg')", name="content_language"),
        sa.CheckConstraint(
            "state IN ('draft','submitted','approved','rejected',"
            "'published','suspended','retired')",
            name="content_state",
        ),
        sa.CheckConstraint(
            "allowed_parameters = '[]'::jsonb", name="content_no_unapproved_parameters"
        ),
        sa.CheckConstraint("octet_length(content_digest) = 32", name="content_sha256"),
        sa.CheckConstraint("revision > 0 AND version > 0", name="content_versions"),
        sa.CheckConstraint(
            "(state IN ('published','suspended','retired')) = (effective_from IS NOT NULL)",
            name="content_publication_required",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="content_publication_interval",
        ),
        ExcludeConstraint(
            ("template_id", "="),
            ("language", "="),
            (sa.text("tstzrange(effective_from, effective_to, '[)')"), "&&"),
            name="ex_content_published_window",
            using="gist",
            where=sa.text("state = 'published'"),
        ),
    )
    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid4)
    template_id: Mapped[UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("message_templates.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    language: Mapped[str] = mapped_column(sa.String(2), nullable=False)
    revision: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    author_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    body_cipher: Mapped[bytes] = mapped_column(sa.LargeBinary, nullable=False)
    allowed_parameters: Mapped[list] = mapped_column(JSONB, nullable=False)
    content_digest: Mapped[bytes] = mapped_column(sa.LargeBinary, nullable=False)
    key_version: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    state: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="draft")
    effective_from: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="1", default=1)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class ContentReview(db.Model):
    __tablename__ = "content_reviews"
    __table_args__ = (
        sa.CheckConstraint("decision IN ('approve','reject')", name="content_review_decision"),
        sa.CheckConstraint("author_id <> reviewer_id", name="content_independent_reviewer"),
        sa.CheckConstraint("clinical_signoff = false", name="content_no_clinical_signoff"),
    )
    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid4)
    version_id: Mapped[UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("template_versions.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    author_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    reviewer_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    decision: Mapped[str] = mapped_column(sa.String(8), nullable=False)
    content_digest: Mapped[bytes] = mapped_column(sa.LargeBinary, nullable=False)
    reason_cipher: Mapped[bytes] = mapped_column(sa.LargeBinary, nullable=False)
    key_version: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    clinical_signoff: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false(), default=False
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class ContentEvent(db.Model):
    __tablename__ = "content_events"
    __table_args__ = (
        sa.UniqueConstraint("version_id", "sequence", name="uq_content_event_sequence"),
        sa.UniqueConstraint("audit_event_id", name="uq_content_event_audit"),
        sa.CheckConstraint("sequence > 0", name="content_event_sequence"),
    )
    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid4)
    version_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("template_versions.id", ondelete="RESTRICT"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    audit_event_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("audit_events.id", name="fk_content_event_audit", ondelete="RESTRICT"),
    )
    event_data_cipher: Mapped[bytes] = mapped_column(sa.LargeBinary, nullable=False)
    key_version: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class ContentIdempotency(db.Model):
    __tablename__ = "content_idempotency"
    __table_args__ = (
        sa.UniqueConstraint("actor_id", "operation", "key", name="uq_content_command_key"),
    )
    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid4)
    actor_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    template_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("message_templates.id", ondelete="RESTRICT"), nullable=False
    )
    version_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("template_versions.id", ondelete="RESTRICT")
    )
    operation: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    request_hash: Mapped[bytes] = mapped_column(sa.LargeBinary, nullable=False)
    response_cipher: Mapped[bytes] = mapped_column(sa.LargeBinary, nullable=False)
    response_status: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    key_version: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
