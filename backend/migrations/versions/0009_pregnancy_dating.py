"""Pregnancy episode idempotency and dating revisions table.

Revision ID: 0009_pregnancy_dating
Revises: 0008_evidence_idempotency
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_pregnancy_dating"
down_revision = "0008_evidence_idempotency"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "tunza_runtime"


def upgrade() -> None:
    # Add idempotency columns to pregnancy_episodes
    op.add_column(
        "pregnancy_episodes",
        sa.Column(
            "facility_id",
            sa.Uuid(),
            sa.ForeignKey(
                "facilities.id",
                name="fk_episodes_facility_id_facilities",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
    )
    op.add_column(
        "pregnancy_episodes",
        sa.Column(
            "idempotency_key",
            sa.String(128),
            nullable=True,
        ),
    )
    op.add_column(
        "pregnancy_episodes",
        sa.Column(
            "request_hash",
            sa.LargeBinary(),
            nullable=False,
            server_default=sa.text("decode('', 'hex')"),
        ),
    )
    op.create_index("ix_pregnancy_episodes_facility_id", "pregnancy_episodes", ["facility_id"])
    op.create_index(
        "ix_pregnancy_episodes_idempotency_key",
        "pregnancy_episodes",
        ["idempotency_key"],
    )
    op.create_unique_constraint(
        "uq_pregnancy_episode_facility_idempotency_key",
        "pregnancy_episodes",
        ["facility_id", "idempotency_key"],
    )
    op.execute(
        "UPDATE pregnancy_episodes SET request_hash = decode('', 'hex') WHERE request_hash IS NULL"
    )

    # Create pregnancy_dating_revisions table
    op.create_table(
        "pregnancy_dating_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("episode_id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("facility_id", sa.Uuid(), nullable=False),
        sa.Column("basis", sa.String(length=32), nullable=False),
        sa.Column(
            "estimated_due_date",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("reason", sa.String(length=1000), nullable=False),
        sa.Column("recorded_by", sa.Uuid(), nullable=False),
        sa.Column("revision_sequence", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
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
            ["episode_id", "patient_id"],
            ["pregnancy_episodes.id", "pregnancy_episodes.patient_id"],
            name="fk_dating_revisions_episode_patient",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id", "facility_id"],
            ["facility_patient_profiles.patient_id", "facility_patient_profiles.facility_id"],
            name="fk_dating_revisions_patient_facility_profiles",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["pregnancy_dating_revisions.id"],
            name="fk_dating_revisions_supersedes_dating_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by"],
            ["users.id"],
            name="fk_dating_revisions_recorded_by_users",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "basis IN ('lmp', 'ultrasound', 'clinical', 'fundal_height')",
            name="ck_pregnancy_dating_revision_basis_values",
        ),
        sa.CheckConstraint(
            "revision_sequence > 0",
            name="ck_pregnancy_dating_revision_sequence_positive",
        ),
        sa.CheckConstraint(
            "version > 0",
            name="ck_pregnancy_dating_revision_version_positive",
        ),
        sa.UniqueConstraint("id", "facility_id", name="uq_pregnancy_dating_revision_id_facility"),
        sa.UniqueConstraint(
            "episode_id",
            "patient_id",
            "revision_sequence",
            name="uq_pregnancy_dating_revision_episode_sequence",
        ),
        sa.Index("ix_pregnancy_dating_revisions_facility_id", "facility_id"),
        sa.Index("ix_pregnancy_dating_revisions_patient_id", "patient_id"),
        sa.Index("ix_pregnancy_dating_revisions_episode_id", "episode_id"),
    )
    op.execute("ALTER TABLE pregnancy_dating_revisions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE pregnancy_dating_revisions FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY pregnancy_dating_revisions_patient_scope ON pregnancy_dating_revisions "
        "USING (patient_id = tunza_current_uuid('tunza.patient_id') "
        "AND facility_id = tunza_current_uuid('tunza.facility_id')) "
        "WITH CHECK (patient_id = tunza_current_uuid('tunza.patient_id') "
        "AND facility_id = tunza_current_uuid('tunza.facility_id'))"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON pregnancy_dating_revisions TO {RUNTIME_ROLE}")


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS pregnancy_dating_revisions_patient_scope "
        "ON pregnancy_dating_revisions"
    )
    op.drop_table("pregnancy_dating_revisions")
    op.drop_constraint(
        "uq_pregnancy_episode_facility_idempotency_key",
        "pregnancy_episodes",
        type_="unique",
    )
    op.drop_index("ix_pregnancy_episodes_idempotency_key", table_name="pregnancy_episodes")
    op.drop_index("ix_pregnancy_episodes_facility_id", table_name="pregnancy_episodes")
    op.drop_column("pregnancy_episodes", "request_hash")
    op.drop_column("pregnancy_episodes", "idempotency_key")
    op.drop_column("pregnancy_episodes", "facility_id")
