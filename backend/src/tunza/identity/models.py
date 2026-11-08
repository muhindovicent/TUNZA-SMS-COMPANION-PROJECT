"""Identity, facility scope, server-side session and append-only audit records."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tunza.extensions import db

ROLE_SCOPE_KINDS = ("facility", "platform", "programme")
USER_STATUSES = ("invited", "active", "disabled")
MEMBERSHIP_STATUSES = ("active", "ended")
SESSION_STATES = ("pending_mfa", "active", "revoked")
AUDIT_OUTCOMES = ("success", "denied", "failure")


class Facility(db.Model):
    __tablename__ = "facilities"
    __table_args__ = (CheckConstraint("version > 0", name="version_positive"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class User(db.Model):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("status IN ('invited', 'active', 'disabled')", name="status_values"),
        CheckConstraint("security_version > 0", name="security_version_positive"),
        CheckConstraint("version > 0", name="version_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    login_identifier: Mapped[str] = mapped_column(String(254), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    security_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class Role(db.Model):
    __tablename__ = "roles"
    __table_args__ = (
        CheckConstraint("scope_kind IN ('facility', 'platform', 'programme')", name="scope_values"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Permission(db.Model):
    __tablename__ = "permissions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


role_permissions = db.Table(
    "role_permissions",
    db.Column(
        "role_id",
        Uuid,
        ForeignKey("roles.id", ondelete="RESTRICT", name="fk_role_permissions_role_id_roles"),
        primary_key=True,
    ),
    db.Column(
        "permission_id",
        Uuid,
        ForeignKey(
            "permissions.id",
            ondelete="RESTRICT",
            name="fk_role_permissions_permission_id_permissions",
        ),
        primary_key=True,
    ),
)


class FacilityMembership(db.Model):
    __tablename__ = "facility_memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "facility_id", name="user_facility"),
        UniqueConstraint("id", "facility_id", name="id_facility"),
        CheckConstraint("status IN ('active', 'ended')", name="status_values"),
        CheckConstraint("ends_at IS NULL OR ends_at > starts_at", name="valid_interval"),
        CheckConstraint("version > 0", name="version_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_facility_memberships_user_id_users"),
        nullable=False,
        index=True,
    )
    facility_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "facilities.id",
            ondelete="RESTRICT",
            name="fk_facility_memberships_facility_id_facilities",
        ),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class RoleBinding(db.Model):
    __tablename__ = "role_bindings"
    __table_args__ = (
        CheckConstraint("scope_kind IN ('facility', 'platform', 'programme')", name="scope_values"),
        CheckConstraint(
            "(scope_kind = 'facility') = (facility_id IS NOT NULL)", name="facility_scope_alignment"
        ),
        CheckConstraint("ends_at IS NULL OR ends_at > starts_at", name="valid_interval"),
        CheckConstraint("version > 0", name="version_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_role_bindings_user_id_users"),
        nullable=False,
        index=True,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("roles.id", ondelete="RESTRICT", name="fk_role_bindings_role_id_roles"),
        nullable=False,
        index=True,
    )
    facility_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "facilities.id", ondelete="RESTRICT", name="fk_role_bindings_facility_id_facilities"
        ),
        nullable=True,
        index=True,
    )
    scope_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    granted_by: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_role_bindings_granted_by_users"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class AuthSession(db.Model):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        CheckConstraint("state IN ('pending_mfa', 'active', 'revoked')", name="state_values"),
        CheckConstraint("security_version > 0", name="security_version_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_auth_sessions_user_id_users"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False, unique=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    security_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuditEvent(db.Model):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("outcome IN ('success', 'denied', 'failure')", name="outcome_values"),
        CheckConstraint("actor_id IS NOT NULL OR service_actor IS NOT NULL", name="actor_required"),
        Index("ix_audit_events_facility_id_created_at", "facility_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_audit_events_actor_id_users"),
        nullable=True,
        index=True,
    )
    service_actor: Mapped[str | None] = mapped_column(String(64), nullable=True)
    facility_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "facilities.id", ondelete="RESTRICT", name="fk_audit_events_facility_id_facilities"
        ),
        nullable=True,
    )
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    permission: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = [
    "AuditEvent",
    "AuthSession",
    "Facility",
    "FacilityMembership",
    "Permission",
    "Role",
    "RoleBinding",
    "User",
    "role_permissions",
]
