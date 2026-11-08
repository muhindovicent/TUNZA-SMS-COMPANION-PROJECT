"""identity and access foundation

Revision ID: 0002_identity_and_access
Revises: 0001_foundation
"""

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_identity_and_access"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None

_SEED_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

ROLES = (
    ("VHT", "VHT", "facility"),
    ("HCW", "Healthcare worker", "facility"),
    ("FA", "Facility administrator", "facility"),
    ("AUTHOR", "Content author", "programme"),
    ("REVIEWER", "Clinical reviewer", "programme"),
    ("PA", "Zest platform administrator", "platform"),
    ("ANALYST", "Programme analyst", "programme"),
)

PERMISSIONS = (
    "facility.read",
    "facility.manage",
    "account.provision",
    "membership.manage",
    "role.delegate",
    "team.manage",
    "assignment.manage",
    "patient.register",
    "patient.read",
    "patient.update",
    "eligibility.record",
    "contact.manage",
    "consent.read",
    "consent.record",
    "consent.withdraw",
    "pregnancy.create",
    "pregnancy.read",
    "pregnancy.date",
    "communication.pause",
    "communication.resume",
    "pregnancy.complete",
    "transfer.initiate",
    "transfer.accept",
    "transfer.cancel",
    "anc_reference.read",
    "appointment.read",
    "appointment.schedule",
    "appointment.confirm",
    "appointment.reschedule",
    "appointment.attend",
    "appointment.cancel",
    "content.read",
    "content.author",
    "content.submit",
    "content.review",
    "content.publish",
    "content.suspend",
    "message.read",
    "message.ops",
    "message.reconcile",
    "messaging.control",
    "inbound.read",
    "inbound.resolve",
    "case.read",
    "case.act",
    "case.assign",
    "dashboard.read",
    "report.aggregate",
    "report.export",
    "audit.read",
)

ROLE_PERMISSIONS = (
    ("VHT", "facility.read"),
    ("VHT", "patient.register"),
    ("VHT", "patient.read"),
    ("VHT", "patient.update"),
    ("VHT", "eligibility.record"),
    ("VHT", "contact.manage"),
    ("VHT", "consent.read"),
    ("VHT", "consent.record"),
    ("VHT", "consent.withdraw"),
    ("VHT", "pregnancy.create"),
    ("VHT", "pregnancy.read"),
    ("VHT", "communication.pause"),
    ("VHT", "communication.resume"),
    ("VHT", "anc_reference.read"),
    ("VHT", "appointment.read"),
    ("VHT", "message.read"),
    ("VHT", "inbound.read"),
    ("VHT", "case.read"),
    ("VHT", "case.act"),
    ("VHT", "dashboard.read"),
    ("HCW", "facility.read"),
    ("HCW", "patient.register"),
    ("HCW", "patient.read"),
    ("HCW", "patient.update"),
    ("HCW", "eligibility.record"),
    ("HCW", "contact.manage"),
    ("HCW", "consent.read"),
    ("HCW", "consent.record"),
    ("HCW", "consent.withdraw"),
    ("HCW", "pregnancy.create"),
    ("HCW", "pregnancy.read"),
    ("HCW", "pregnancy.date"),
    ("HCW", "communication.pause"),
    ("HCW", "communication.resume"),
    ("HCW", "pregnancy.complete"),
    ("HCW", "transfer.initiate"),
    ("HCW", "transfer.cancel"),
    ("HCW", "anc_reference.read"),
    ("HCW", "appointment.read"),
    ("HCW", "appointment.schedule"),
    ("HCW", "appointment.confirm"),
    ("HCW", "appointment.reschedule"),
    ("HCW", "appointment.attend"),
    ("HCW", "appointment.cancel"),
    ("HCW", "message.read"),
    ("HCW", "inbound.read"),
    ("HCW", "inbound.resolve"),
    ("HCW", "case.read"),
    ("HCW", "case.act"),
    ("HCW", "dashboard.read"),
    ("FA", "facility.read"),
    ("FA", "account.provision"),
    ("FA", "membership.manage"),
    ("FA", "role.delegate"),
    ("FA", "team.manage"),
    ("FA", "assignment.manage"),
    ("FA", "transfer.accept"),
    ("FA", "transfer.cancel"),
    ("FA", "anc_reference.read"),
    ("FA", "case.assign"),
    ("FA", "dashboard.read"),
    ("FA", "report.aggregate"),
    ("AUTHOR", "anc_reference.read"),
    ("AUTHOR", "content.read"),
    ("AUTHOR", "content.author"),
    ("AUTHOR", "content.submit"),
    ("AUTHOR", "dashboard.read"),
    ("REVIEWER", "anc_reference.read"),
    ("REVIEWER", "content.read"),
    ("REVIEWER", "content.review"),
    ("REVIEWER", "content.publish"),
    ("REVIEWER", "content.suspend"),
    ("REVIEWER", "dashboard.read"),
    ("PA", "facility.read"),
    ("PA", "facility.manage"),
    ("PA", "account.provision"),
    ("PA", "membership.manage"),
    ("PA", "role.delegate"),
    ("PA", "message.ops"),
    ("PA", "message.reconcile"),
    ("PA", "messaging.control"),
    ("PA", "inbound.read"),
    ("PA", "dashboard.read"),
    ("PA", "report.aggregate"),
    ("ANALYST", "facility.read"),
    ("ANALYST", "dashboard.read"),
    ("ANALYST", "report.aggregate"),
)


def _seeded_id(kind: str, value: str) -> uuid.UUID:
    return uuid.uuid5(_SEED_NAMESPACE, f"tunza:{kind}:{value}")


def _seed_roles_and_permissions() -> None:
    roles = sa.table(
        "roles",
        sa.column("id", sa.Uuid),
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("scope_kind", sa.String),
    )
    permissions = sa.table(
        "permissions",
        sa.column("id", sa.Uuid),
        sa.column("code", sa.String),
        sa.column("description", sa.String),
    )
    role_permissions = sa.table(
        "role_permissions",
        sa.column("role_id", sa.Uuid),
        sa.column("permission_id", sa.Uuid),
    )

    op.bulk_insert(
        roles,
        [
            {"id": _seeded_id("role", code), "code": code, "name": name, "scope_kind": scope}
            for code, name, scope in ROLES
        ],
    )
    op.bulk_insert(
        permissions,
        [
            {
                "id": _seeded_id("permission", code),
                "code": code,
                "description": code.replace(".", " "),
            }
            for code in PERMISSIONS
        ],
    )
    op.bulk_insert(
        role_permissions,
        [
            {
                "role_id": _seeded_id("role", role_code),
                "permission_id": _seeded_id("permission", permission_code),
            }
            for role_code, permission_code in ROLE_PERMISSIONS
        ],
    )


def _create_audit_append_only_trigger() -> None:
    op.execute(
        """
        CREATE FUNCTION tunza_audit_events_append_only() RETURNS trigger AS $tunza_audit$
        BEGIN
            RAISE EXCEPTION 'audit_events rows are append-only';
        END;
        $tunza_audit$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_append_only
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION tunza_audit_events_append_only()
        """
    )


def upgrade() -> None:
    op.create_table(
        "facilities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint("version > 0", name=op.f("ck_facilities_version_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_facilities")),
        sa.UniqueConstraint("code", name=op.f("uq_facilities_code")),
    )
    op.create_table(
        "permissions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_permissions")),
        sa.UniqueConstraint("code", name=op.f("uq_permissions_code")),
    )
    op.create_table(
        "roles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("scope_kind", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "scope_kind IN ('facility', 'platform', 'programme')",
            name=op.f("ck_roles_scope_values"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_roles")),
        sa.UniqueConstraint("code", name=op.f("uq_roles_code")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("login_identifier", sa.String(length=254), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("security_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "status IN ('invited', 'active', 'disabled')", name=op.f("ck_users_status_values")
        ),
        sa.CheckConstraint("security_version > 0", name=op.f("ck_users_security_version_positive")),
        sa.CheckConstraint("version > 0", name=op.f("ck_users_version_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("login_identifier", name=op.f("uq_users_login_identifier")),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("service_actor", sa.String(length=64), nullable=True),
        sa.Column("facility_id", sa.Uuid(), nullable=True),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("permission", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ('success', 'denied', 'failure')",
            name=op.f("ck_audit_events_outcome_values"),
        ),
        sa.CheckConstraint(
            "actor_id IS NOT NULL OR service_actor IS NOT NULL",
            name=op.f("ck_audit_events_actor_required"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], name="fk_audit_events_actor_id_users", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["facility_id"],
            ["facilities.id"],
            name="fk_audit_events_facility_id_facilities",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index(op.f("ix_audit_events_actor_id"), "audit_events", ["actor_id"], unique=False)
    op.create_index(
        "ix_audit_events_facility_id_created_at",
        "audit_events",
        ["facility_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_audit_events_resource_id"), "audit_events", ["resource_id"], unique=False
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("security_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('pending_mfa', 'active', 'revoked')",
            name=op.f("ck_auth_sessions_state_values"),
        ),
        sa.CheckConstraint(
            "security_version > 0", name=op.f("ck_auth_sessions_security_version_positive")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_auth_sessions_user_id_users", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_auth_sessions_token_hash")),
    )
    op.create_index(op.f("ix_auth_sessions_user_id"), "auth_sessions", ["user_id"], unique=False)
    op.create_table(
        "facility_memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("facility_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'ended')", name=op.f("ck_facility_memberships_status_values")
        ),
        sa.CheckConstraint(
            "ends_at IS NULL OR ends_at > starts_at",
            name=op.f("ck_facility_memberships_valid_interval"),
        ),
        sa.CheckConstraint("version > 0", name=op.f("ck_facility_memberships_version_positive")),
        sa.ForeignKeyConstraint(
            ["facility_id"],
            ["facilities.id"],
            name="fk_facility_memberships_facility_id_facilities",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_facility_memberships_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_facility_memberships")),
        sa.UniqueConstraint("id", "facility_id", name="id_facility"),
        sa.UniqueConstraint("user_id", "facility_id", name="user_facility"),
    )
    op.create_index(
        op.f("ix_facility_memberships_facility_id"),
        "facility_memberships",
        ["facility_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_facility_memberships_user_id"), "facility_memberships", ["user_id"], unique=False
    )
    op.create_table(
        "role_bindings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("facility_id", sa.Uuid(), nullable=True),
        sa.Column("scope_kind", sa.String(length=16), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("granted_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "(scope_kind = 'facility') = (facility_id IS NOT NULL)",
            name=op.f("ck_role_bindings_facility_scope_alignment"),
        ),
        sa.CheckConstraint(
            "scope_kind IN ('facility', 'platform', 'programme')",
            name=op.f("ck_role_bindings_scope_values"),
        ),
        sa.CheckConstraint(
            "ends_at IS NULL OR ends_at > starts_at", name=op.f("ck_role_bindings_valid_interval")
        ),
        sa.CheckConstraint("version > 0", name=op.f("ck_role_bindings_version_positive")),
        sa.ForeignKeyConstraint(
            ["facility_id"],
            ["facilities.id"],
            name="fk_role_bindings_facility_id_facilities",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by"],
            ["users.id"],
            name="fk_role_bindings_granted_by_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["roles.id"], name="fk_role_bindings_role_id_roles", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_role_bindings_user_id_users", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role_bindings")),
    )
    op.create_index(
        op.f("ix_role_bindings_facility_id"), "role_bindings", ["facility_id"], unique=False
    )
    op.create_index(op.f("ix_role_bindings_role_id"), "role_bindings", ["role_id"], unique=False)
    op.create_index(op.f("ix_role_bindings_user_id"), "role_bindings", ["user_id"], unique=False)
    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("permission_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["permission_id"],
            ["permissions.id"],
            name="fk_role_permissions_permission_id_permissions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["roles.id"], name="fk_role_permissions_role_id_roles", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("role_id", "permission_id", name=op.f("pk_role_permissions")),
    )
    _seed_roles_and_permissions()
    _create_audit_append_only_trigger()


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_append_only ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS tunza_audit_events_append_only()")
    op.drop_table("role_permissions")
    op.drop_index(op.f("ix_role_bindings_user_id"), table_name="role_bindings")
    op.drop_index(op.f("ix_role_bindings_role_id"), table_name="role_bindings")
    op.drop_index(op.f("ix_role_bindings_facility_id"), table_name="role_bindings")
    op.drop_table("role_bindings")
    op.drop_index(op.f("ix_facility_memberships_user_id"), table_name="facility_memberships")
    op.drop_index(op.f("ix_facility_memberships_facility_id"), table_name="facility_memberships")
    op.drop_table("facility_memberships")
    op.drop_index(op.f("ix_auth_sessions_user_id"), table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index(op.f("ix_audit_events_resource_id"), table_name="audit_events")
    op.drop_index("ix_audit_events_facility_id_created_at", table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_actor_id"), table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("users")
    op.drop_table("roles")
    op.drop_table("permissions")
    op.drop_table("facilities")
