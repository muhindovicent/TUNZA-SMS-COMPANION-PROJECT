"""protected patient registry foundation

Revision ID: 0004_patient_registry
Revises: 0003_staff_administration
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_patient_registry"
down_revision = "0003_staff_administration"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "tunza_runtime"


def _create_runtime_role() -> None:
    op.execute(
        f"""
        DO $tunza_role$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
                CREATE ROLE {RUNTIME_ROLE} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                    NOREPLICATION NOBYPASSRLS;
            ELSE
                ALTER ROLE {RUNTIME_ROLE} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                    NOREPLICATION NOBYPASSRLS;
            END IF;
        END
        $tunza_role$;
        """
    )
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {RUNTIME_ROLE}")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {RUNTIME_ROLE}")


def _create_context_function() -> None:
    op.execute(
        """
        CREATE FUNCTION tunza_current_uuid(setting_name text) RETURNS uuid
        LANGUAGE plpgsql STABLE PARALLEL SAFE SECURITY INVOKER
        SET search_path = pg_catalog
        AS $tunza_rls$
        DECLARE
            raw_value text;
        BEGIN
            raw_value := current_setting(setting_name, true);
            IF raw_value IS NULL OR raw_value = '' THEN
                RETURN NULL;
            END IF;
            RETURN raw_value::uuid;
        EXCEPTION WHEN invalid_text_representation THEN
            RETURN NULL;
        END;
        $tunza_rls$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION tunza_current_uuid(text) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION tunza_current_uuid(text) TO {RUNTIME_ROLE}")


def _enable_rls(table: str, using: str, check: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_runtime_scope ON {table} USING ({using}) WITH CHECK ({check})"
    )


def _grant_runtime_privileges() -> None:
    op.execute(
        f"""
        GRANT SELECT, INSERT, UPDATE ON facilities, users, auth_sessions,
            facility_memberships, role_bindings TO {RUNTIME_ROLE};
        GRANT SELECT ON roles, permissions, role_permissions TO {RUNTIME_ROLE};
        GRANT SELECT, INSERT ON audit_events TO {RUNTIME_ROLE};
        REVOKE UPDATE, DELETE ON audit_events FROM {RUNTIME_ROLE};
        REVOKE ALL ON alembic_version FROM {RUNTIME_ROLE};
        GRANT SELECT, INSERT ON patients, facility_patient_profiles,
            communication_preferences, pregnancy_episodes,
            episode_facility_assignments, participant_assignments TO {RUNTIME_ROLE};
        """
    )


def upgrade() -> None:
    _create_runtime_role()
    op.create_table(
        "patients",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_patients")),
    )
    op.create_table(
        "facility_patient_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("facility_id", sa.Uuid(), nullable=False),
        sa.Column("local_reference", sa.String(length=200), nullable=False),
        sa.Column("demographics_cipher", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
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
            "status = 'synthetic'", name=op.f("ck_facility_patient_profiles_synthetic_status_only")
        ),
        sa.CheckConstraint(
            "version > 0", name=op.f("ck_facility_patient_profiles_version_positive")
        ),
        sa.ForeignKeyConstraint(
            ["facility_id"],
            ["facilities.id"],
            name="fk_profiles_facility_id_facilities",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name="fk_profiles_patient_id_patients",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_facility_patient_profiles")),
        sa.UniqueConstraint("facility_id", "local_reference", name="profile_facility_reference"),
        sa.UniqueConstraint("patient_id", "facility_id", name="profile_patient_facility"),
    )
    op.create_index(
        op.f("ix_facility_patient_profiles_facility_id"),
        "facility_patient_profiles",
        ["facility_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_facility_patient_profiles_patient_id"),
        "facility_patient_profiles",
        ["patient_id"],
        unique=False,
    )
    op.create_table(
        "communication_preferences",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("facility_id", sa.Uuid(), nullable=False),
        sa.Column("language", sa.String(length=2), nullable=False),
        sa.Column(
            "privacy_flags",
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "language IN ('en', 'lg')",
            name=op.f("ck_communication_preferences_language_values"),
        ),
        sa.CheckConstraint(
            "privacy_flags = '{}'::jsonb",
            name=op.f("ck_communication_preferences_privacy_policy_unconfigured"),
        ),
        sa.CheckConstraint(
            "version > 0", name=op.f("ck_communication_preferences_version_positive")
        ),
        sa.ForeignKeyConstraint(
            ["patient_id", "facility_id"],
            ["facility_patient_profiles.patient_id", "facility_patient_profiles.facility_id"],
            name="fk_preferences_patient_facility_profiles",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_communication_preferences")),
        sa.UniqueConstraint("patient_id", "facility_id", name="preference_patient_facility"),
    )
    op.create_index(
        op.f("ix_communication_preferences_facility_id"),
        "communication_preferences",
        ["facility_id"],
        unique=False,
    )
    op.create_table(
        "pregnancy_episodes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("lifecycle", sa.String(length=16), nullable=False),
        sa.Column("communication_state", sa.String(length=16), nullable=False),
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
            "communication_state IN ('active', 'paused', 'completed', 'withdrawn')",
            name=op.f("ck_pregnancy_episodes_communication_state_values"),
        ),
        sa.CheckConstraint(
            "lifecycle IN ('ongoing', 'completed')",
            name=op.f("ck_pregnancy_episodes_lifecycle_values"),
        ),
        sa.CheckConstraint("version > 0", name=op.f("ck_pregnancy_episodes_version_positive")),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name="fk_episodes_patient_id_patients",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pregnancy_episodes")),
        sa.UniqueConstraint("id", "patient_id", name="episode_id_patient"),
    )
    op.create_index(
        op.f("ix_pregnancy_episodes_patient_id"),
        "pregnancy_episodes",
        ["patient_id"],
        unique=False,
    )
    op.create_table(
        "episode_facility_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("episode_id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("facility_id", sa.Uuid(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
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
            "valid_to IS NULL OR valid_to > valid_from",
            name=op.f("ck_episode_facility_assignments_valid_interval"),
        ),
        sa.CheckConstraint(
            "version > 0", name=op.f("ck_episode_facility_assignments_version_positive")
        ),
        sa.ForeignKeyConstraint(
            ["episode_id", "patient_id"],
            ["pregnancy_episodes.id", "pregnancy_episodes.patient_id"],
            name="fk_episode_assignments_episode_patient",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id", "facility_id"],
            ["facility_patient_profiles.patient_id", "facility_patient_profiles.facility_id"],
            name="fk_episode_assignments_patient_facility",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_episode_facility_assignments")),
        sa.UniqueConstraint(
            "id", "episode_id", "facility_id", name="episode_assignment_id_episode_facility"
        ),
        sa.UniqueConstraint("id", "facility_id", name="episode_assignment_id_facility"),
    )
    op.create_index(
        op.f("ix_episode_facility_assignments_episode_id"),
        "episode_facility_assignments",
        ["episode_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_episode_facility_assignments_facility_id"),
        "episode_facility_assignments",
        ["facility_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_episode_facility_assignments_patient_id"),
        "episode_facility_assignments",
        ["patient_id"],
        unique=False,
    )
    op.create_index(
        "uq_episode_facility_assignments_current_episode",
        "episode_facility_assignments",
        ["episode_id"],
        unique=True,
        postgresql_where=sa.text("valid_to IS NULL"),
    )
    op.create_table(
        "participant_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("episode_facility_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("facility_id", sa.Uuid(), nullable=False),
        sa.Column("membership_id", sa.Uuid(), nullable=False),
        sa.Column("duty", sa.String(length=32), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
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
            "duty = 'primary'",
            name=op.f("ck_participant_assignments_individual_primary_duty_only"),
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name=op.f("ck_participant_assignments_valid_interval"),
        ),
        sa.CheckConstraint("version > 0", name=op.f("ck_participant_assignments_version_positive")),
        sa.ForeignKeyConstraint(
            ["episode_facility_assignment_id", "facility_id"],
            ["episode_facility_assignments.id", "episode_facility_assignments.facility_id"],
            name="fk_participant_assignments_episode_facility",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["membership_id", "facility_id"],
            ["facility_memberships.id", "facility_memberships.facility_id"],
            name="fk_participant_assignments_membership_facility",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_participant_assignments")),
    )
    op.create_index(
        op.f("ix_participant_assignments_episode_facility_assignment_id"),
        "participant_assignments",
        ["episode_facility_assignment_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_participant_assignments_facility_id"),
        "participant_assignments",
        ["facility_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_participant_assignments_membership_id"),
        "participant_assignments",
        ["membership_id"],
        unique=False,
    )
    op.create_index(
        "uq_participant_assignments_current_membership_duty",
        "participant_assignments",
        ["episode_facility_assignment_id", "membership_id", "duty"],
        unique=True,
        postgresql_where=sa.text("valid_to IS NULL"),
    )

    _create_context_function()
    actor = "tunza_current_uuid('tunza.actor_id') IS NOT NULL"
    facility = "tunza_current_uuid('tunza.facility_id')"
    _enable_rls(
        "patients",
        f"{actor} AND EXISTS (SELECT 1 FROM facility_patient_profiles profile "
        f"WHERE profile.patient_id = patients.id AND profile.facility_id = {facility})",
        f"{actor} AND {facility} IS NOT NULL",
    )
    for table in (
        "facility_patient_profiles",
        "communication_preferences",
        "episode_facility_assignments",
        "participant_assignments",
    ):
        _enable_rls(
            table,
            f"{actor} AND facility_id = {facility}",
            f"{actor} AND facility_id = {facility}",
        )
    _enable_rls(
        "pregnancy_episodes",
        f"{actor} AND EXISTS (SELECT 1 FROM episode_facility_assignments assignment "
        f"WHERE assignment.episode_id = pregnancy_episodes.id "
        f"AND assignment.facility_id = {facility})",
        f"{actor} AND {facility} IS NOT NULL",
    )
    _grant_runtime_privileges()


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS participant_assignments_runtime_scope ON participant_assignments"
    )
    op.execute(
        "DROP POLICY IF EXISTS episode_facility_assignments_runtime_scope "
        "ON episode_facility_assignments"
    )
    op.execute("DROP POLICY IF EXISTS pregnancy_episodes_runtime_scope ON pregnancy_episodes")
    op.execute(
        "DROP POLICY IF EXISTS communication_preferences_runtime_scope ON communication_preferences"
    )
    op.execute(
        "DROP POLICY IF EXISTS facility_patient_profiles_runtime_scope ON facility_patient_profiles"
    )
    op.execute("DROP POLICY IF EXISTS patients_runtime_scope ON patients")
    op.drop_index(
        "uq_participant_assignments_current_membership_duty",
        table_name="participant_assignments",
        postgresql_where=sa.text("valid_to IS NULL"),
    )
    op.drop_index(
        op.f("ix_participant_assignments_membership_id"), table_name="participant_assignments"
    )
    op.drop_index(
        op.f("ix_participant_assignments_facility_id"), table_name="participant_assignments"
    )
    op.drop_index(
        op.f("ix_participant_assignments_episode_facility_assignment_id"),
        table_name="participant_assignments",
    )
    op.drop_table("participant_assignments")
    op.drop_index(
        "uq_episode_facility_assignments_current_episode",
        table_name="episode_facility_assignments",
        postgresql_where=sa.text("valid_to IS NULL"),
    )
    op.drop_index(
        op.f("ix_episode_facility_assignments_patient_id"),
        table_name="episode_facility_assignments",
    )
    op.drop_index(
        op.f("ix_episode_facility_assignments_facility_id"),
        table_name="episode_facility_assignments",
    )
    op.drop_index(
        op.f("ix_episode_facility_assignments_episode_id"),
        table_name="episode_facility_assignments",
    )
    op.drop_table("episode_facility_assignments")
    op.drop_index(op.f("ix_pregnancy_episodes_patient_id"), table_name="pregnancy_episodes")
    op.drop_table("pregnancy_episodes")
    op.drop_index(
        op.f("ix_communication_preferences_facility_id"),
        table_name="communication_preferences",
    )
    op.drop_table("communication_preferences")
    op.drop_index(
        op.f("ix_facility_patient_profiles_patient_id"),
        table_name="facility_patient_profiles",
    )
    op.drop_index(
        op.f("ix_facility_patient_profiles_facility_id"),
        table_name="facility_patient_profiles",
    )
    op.drop_table("facility_patient_profiles")
    op.drop_table("patients")
    op.execute("DROP FUNCTION IF EXISTS tunza_current_uuid(text)")
