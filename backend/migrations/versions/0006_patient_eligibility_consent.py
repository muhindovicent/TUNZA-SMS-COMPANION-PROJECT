"""Patient eligibility and consent management tables.

Revision ID: 0006_patient_eligibility_consent
Revises: 0005_patient_idempotency
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_patient_eligibility_consent"
down_revision = "0005_patient_idempotency"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "tunza_runtime"


def _enable_rls(table: str, using: str, check: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_runtime_scope ON {table} USING ({using}) WITH CHECK ({check})"
    )


def _grant_runtime(table: str) -> None:
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO {RUNTIME_ROLE}")


def upgrade() -> None:
    op.create_table(
        "protected_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "facility_id",
            sa.Uuid(),
            sa.ForeignKey(
                "facilities.id",
                name="fk_protected_evidence_facility_id_facilities",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column("storage_reference", sa.String(500), nullable=False),
        sa.Column("digest", sa.LargeBinary(), nullable=False),
        sa.Column("media_type", sa.String(64), nullable=False),
        sa.Column(
            "recorded_by",
            sa.Uuid(),
            sa.ForeignKey(
                "users.id", name="fk_protected_evidence_recorded_by_users", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.UniqueConstraint("id", "facility_id", name="uq_protected_evidence_id_facility"),
        sa.Index("ix_protected_evidence_facility_id", "facility_id"),
    )
    _enable_rls(
        "protected_evidence",
        "facility_id = tunza_current_uuid('tunza.facility_id')",
        "facility_id = tunza_current_uuid('tunza.facility_id')",
    )
    _grant_runtime("protected_evidence")

    op.create_table(
        "consent_notice_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("notice_code", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(2), nullable=False),
        sa.Column("text_cipher", sa.LargeBinary(), nullable=False),
        sa.Column(
            "purpose_codes",
            postgresql.ARRAY(sa.String(64)),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="'approved'"),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.CheckConstraint(
            "language IN ('en', 'lg')",
            name="ck_consent_notice_version_language_values",
        ),
        sa.CheckConstraint(
            "status IN ('approved', 'rejected', 'superseded')",
            name="ck_consent_notice_version_status_values",
        ),
        sa.CheckConstraint("version > 0", name="ck_consent_notice_version_version_positive"),
        sa.UniqueConstraint(
            "notice_code",
            "revision",
            "language",
            name="uq_consent_notice_version_code_revision_language",
        ),
    )
    _enable_rls(
        "consent_notice_versions",
        "tunza_current_uuid('tunza.actor_id') IS NOT NULL",
        "tunza_current_uuid('tunza.actor_id') IS NOT NULL",
    )
    op.execute(f"GRANT SELECT ON consent_notice_versions TO {RUNTIME_ROLE}")
    op.execute(f"GRANT INSERT ON consent_notice_versions TO {RUNTIME_ROLE}")

    op.create_table(
        "adult_eligibility_checks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("facility_id", sa.Uuid(), nullable=False),
        sa.Column("method", sa.String(64), nullable=False),
        sa.Column("result", sa.String(32), nullable=False),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "checked_by",
            sa.Uuid(),
            sa.ForeignKey(
                "users.id", name="fk_adult_eligibility_checked_by_users", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column("evidence_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id", "facility_id"],
            ["facility_patient_profiles.patient_id", "facility_patient_profiles.facility_id"],
            name="fk_adult_eligibility_patient_facility_profiles",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id", "facility_id"],
            ["protected_evidence.id", "protected_evidence.facility_id"],
            name="fk_adult_eligibility_evidence_protected_evidence",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "result IN ('verified', 'uncertain', 'ineligible')",
            name="ck_adult_eligibility_result_values",
        ),
        sa.CheckConstraint("sequence > 0", name="ck_adult_eligibility_sequence_positive"),
        sa.CheckConstraint("version > 0", name="ck_adult_eligibility_version_positive"),
        sa.UniqueConstraint(
            "patient_id",
            "facility_id",
            "sequence",
            name="uq_adult_eligibility_patient_facility_sequence",
        ),
        sa.UniqueConstraint(
            "facility_id",
            "idempotency_key",
            name="uq_adult_eligibility_facility_idempotency_key",
        ),
        sa.Index("ix_adult_eligibility_checks_facility_id", "facility_id"),
        sa.Index("ix_adult_eligibility_checks_patient_id", "patient_id"),
        sa.Index("ix_adult_eligibility_checks_idempotency_key", "idempotency_key"),
    )
    _enable_rls(
        "adult_eligibility_checks",
        "facility_id = tunza_current_uuid('tunza.facility_id')",
        "facility_id = tunza_current_uuid('tunza.facility_id')",
    )
    _grant_runtime("adult_eligibility_checks")

    op.create_table(
        "consent_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("facility_id", sa.Uuid(), nullable=False),
        sa.Column("episode_id", sa.Uuid(), nullable=True),
        sa.Column("notice_version_id", sa.Uuid(), nullable=True),
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("method", sa.String(64), nullable=False),
        sa.Column("language", sa.String(2), nullable=False),
        sa.Column(
            "effective_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "recorded_by",
            sa.Uuid(),
            sa.ForeignKey(
                "users.id", name="fk_consent_event_recorded_by_users", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column("evidence_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id", "facility_id"],
            ["facility_patient_profiles.patient_id", "facility_patient_profiles.facility_id"],
            name="fk_consent_event_patient_facility_profiles",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["episode_id", "patient_id"],
            ["pregnancy_episodes.id", "pregnancy_episodes.patient_id"],
            name="fk_consent_event_episode_patient",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["notice_version_id"],
            ["consent_notice_versions.id"],
            name="fk_consent_event_notice_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id", "facility_id"],
            ["protected_evidence.id", "protected_evidence.facility_id"],
            name="fk_consent_event_evidence_protected_evidence",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["consent_events.id"],
            name="fk_consent_event_supersedes_consent_event",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "decision IN ('grant', 'withdraw', 'correct')",
            name="ck_consent_event_decision_values",
        ),
        sa.CheckConstraint(
            "language IN ('en', 'lg')",
            name="ck_consent_event_language_values",
        ),
        sa.CheckConstraint("event_sequence > 0", name="ck_consent_event_sequence_positive"),
        sa.CheckConstraint("version > 0", name="ck_consent_event_version_positive"),
        sa.UniqueConstraint(
            "patient_id",
            "facility_id",
            "event_sequence",
            name="uq_consent_event_patient_facility_sequence",
        ),
        sa.UniqueConstraint(
            "facility_id",
            "idempotency_key",
            name="uq_consent_event_facility_idempotency_key",
        ),
        sa.Index("ix_consent_events_facility_id", "facility_id"),
        sa.Index("ix_consent_events_patient_id", "patient_id"),
        sa.Index("ix_consent_events_idempotency_key", "idempotency_key"),
    )
    _enable_rls(
        "consent_events",
        "facility_id = tunza_current_uuid('tunza.facility_id')",
        "facility_id = tunza_current_uuid('tunza.facility_id')",
    )
    _grant_runtime("consent_events")

    # Append-only triggers for adult_eligibility_checks and consent_events
    op.execute(
        """
        CREATE FUNCTION adult_eligibility_checks_append_only()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'adult_eligibility_checks rows are append-only';
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER adult_eligibility_checks_append_only "
        "BEFORE UPDATE OR DELETE ON adult_eligibility_checks "
        "FOR EACH ROW EXECUTE FUNCTION adult_eligibility_checks_append_only()"
    )
    op.execute(
        """
        CREATE FUNCTION consent_events_append_only()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'consent_events rows are append-only';
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER consent_events_append_only "
        "BEFORE UPDATE OR DELETE ON consent_events "
        "FOR EACH ROW EXECUTE FUNCTION consent_events_append_only()"
    )

    # Append-only trigger for consent_notice_versions (publication restricted to INSERT only)
    op.execute(
        """
        CREATE FUNCTION consent_notice_versions_append_only()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'consent_notice_versions rows are append-only';
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER consent_notice_versions_append_only "
        "BEFORE UPDATE OR DELETE ON consent_notice_versions "
        "FOR EACH ROW EXECUTE FUNCTION consent_notice_versions_append_only()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS consent_notice_versions_append_only ON consent_notice_versions"
    )
    op.execute("DROP FUNCTION IF EXISTS consent_notice_versions_append_only()")
    op.execute("DROP TRIGGER IF EXISTS consent_events_append_only ON consent_events")
    op.execute("DROP FUNCTION IF EXISTS consent_events_append_only()")
    op.execute(
        "DROP TRIGGER IF EXISTS adult_eligibility_checks_append_only ON adult_eligibility_checks"
    )
    op.execute("DROP FUNCTION IF EXISTS adult_eligibility_checks_append_only()")
    op.execute("DROP POLICY IF EXISTS consent_events_runtime_scope ON consent_events")
    op.execute(
        "DROP POLICY IF EXISTS adult_eligibility_checks_runtime_scope ON adult_eligibility_checks"
    )
    op.execute(
        "DROP POLICY IF EXISTS consent_notice_versions_runtime_scope ON consent_notice_versions"
    )
    op.execute("DROP POLICY IF EXISTS protected_evidence_runtime_scope ON protected_evidence")
    op.drop_table("consent_events")
    op.drop_table("adult_eligibility_checks")
    op.drop_table("consent_notice_versions")
    op.drop_table("protected_evidence")
