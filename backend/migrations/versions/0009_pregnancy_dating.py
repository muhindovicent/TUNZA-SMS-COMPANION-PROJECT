"""Pregnancy creation safeguards and append-only synthetic dating history.

Revision ID: 0009_pregnancy_dating
Revises: 0008_evidence_idempotency
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_pregnancy_dating"
down_revision = "0008_evidence_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Never guess a facility, delete duplicate episodes or invent a completion.
    # Invalid legacy data aborts this transactional migration without changing it.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT patient_id FROM pregnancy_episodes
                       WHERE lifecycle = 'ongoing' GROUP BY patient_id HAVING count(*) > 1)
            THEN RAISE EXCEPTION 'Resolve duplicate ongoing synthetic episodes before 0009';
            END IF;
            IF EXISTS (SELECT 1 FROM pregnancy_episodes e WHERE NOT EXISTS
                       (SELECT 1 FROM episode_facility_assignments a WHERE a.episode_id = e.id))
            THEN RAISE EXCEPTION 'Resolve unassigned synthetic episodes before 0009';
            END IF;
        END $$
    """)
    op.add_column("pregnancy_episodes", sa.Column("facility_id", sa.Uuid(), nullable=True))
    op.add_column("pregnancy_episodes", sa.Column("idempotency_key", sa.String(128)))
    op.add_column("pregnancy_episodes", sa.Column("request_hash", sa.LargeBinary()))
    op.add_column("pregnancy_episodes", sa.Column("created_by", sa.Uuid()))
    # Origin facility is provenance only; live authorization uses responsibility
    # and participant assignments. Closed historical assignments also backfill.
    op.execute("""
        UPDATE pregnancy_episodes e SET facility_id = (
            SELECT a.facility_id FROM episode_facility_assignments a
            WHERE a.episode_id = e.id ORDER BY a.valid_from, a.id LIMIT 1
        ), request_hash = decode('', 'hex')
    """)
    op.alter_column("pregnancy_episodes", "facility_id", nullable=False)
    op.alter_column("pregnancy_episodes", "request_hash", nullable=False)
    op.create_foreign_key(
        "fk_episodes_facility_id_facilities",
        "pregnancy_episodes",
        "facilities",
        ["facility_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_episodes_created_by_users",
        "pregnancy_episodes",
        "users",
        ["created_by"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_pregnancy_episodes_facility_id", "pregnancy_episodes", ["facility_id"])
    op.create_index(
        "ix_pregnancy_episodes_idempotency_key", "pregnancy_episodes", ["idempotency_key"]
    )
    op.create_unique_constraint(
        "uq_pregnancy_episode_facility_idempotency_key",
        "pregnancy_episodes",
        ["facility_id", "idempotency_key"],
    )
    op.create_index(
        "uq_pregnancy_episode_ongoing_patient",
        "pregnancy_episodes",
        ["patient_id"],
        unique=True,
        postgresql_where=sa.text("lifecycle = 'ongoing'"),
    )

    # SECURITY INVOKER: keep the underlying facility RLS and verify the actor's
    # current individual assignment and membership, including validity windows.
    op.execute("""
        CREATE FUNCTION tunza_pregnancy_assigned(target uuid) RETURNS boolean
        LANGUAGE sql STABLE SECURITY INVOKER SET search_path = public, pg_catalog AS $$
            SELECT EXISTS (
                SELECT 1 FROM episode_facility_assignments a
                JOIN participant_assignments p ON p.episode_facility_assignment_id = a.id
                     AND p.facility_id = a.facility_id
                JOIN facility_memberships m ON m.id = p.membership_id
                     AND m.facility_id = a.facility_id
                WHERE a.episode_id = target
                  AND a.facility_id = tunza_current_uuid('tunza.facility_id')
                  AND m.user_id = tunza_current_uuid('tunza.actor_id')
                  AND m.status = 'active' AND m.starts_at <= statement_timestamp()
                  AND (m.ends_at IS NULL OR m.ends_at > statement_timestamp())
                  AND a.valid_from <= statement_timestamp()
                  AND (a.valid_to IS NULL OR a.valid_to > statement_timestamp())
                  AND p.valid_from <= statement_timestamp()
                  AND (p.valid_to IS NULL OR p.valid_to > statement_timestamp())
            )
        $$
    """)
    op.execute("REVOKE ALL ON FUNCTION tunza_pregnancy_assigned(uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION tunza_pregnancy_assigned(uuid) TO tunza_runtime")
    op.execute("DROP POLICY pregnancy_episodes_runtime_scope ON pregnancy_episodes")
    op.execute("""
        CREATE POLICY pregnancy_episodes_read ON pregnancy_episodes FOR SELECT
        USING (tunza_pregnancy_assigned(id))
    """)
    op.execute("""
        CREATE POLICY pregnancy_episodes_insert ON pregnancy_episodes FOR INSERT WITH CHECK (
            facility_id = tunza_current_uuid('tunza.facility_id')
            AND created_by = tunza_current_uuid('tunza.actor_id')
            AND EXISTS (SELECT 1 FROM facility_patient_profiles p
                WHERE p.patient_id = pregnancy_episodes.patient_id
                AND p.facility_id = pregnancy_episodes.facility_id
                AND (
                    -- Initial registration creates the profile in this very
                    -- transaction; subsequent episodes require assigned care.
                    p.xmin = pg_current_xact_id()::xid
                    OR EXISTS (SELECT 1 FROM episode_facility_assignments a
                        WHERE a.patient_id = pregnancy_episodes.patient_id
                          AND tunza_pregnancy_assigned(a.episode_id))
                ))
        )
    """)
    op.execute("""
        CREATE POLICY pregnancy_episodes_update ON pregnancy_episodes FOR UPDATE
        USING (tunza_pregnancy_assigned(id)) WITH CHECK (tunza_pregnancy_assigned(id))
    """)
    op.execute("GRANT UPDATE (version, updated_at) ON pregnancy_episodes TO tunza_runtime")

    op.create_table(
        "pregnancy_dating_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("episode_id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("facility_id", sa.Uuid(), nullable=False),
        sa.Column("basis", sa.String(64), nullable=False),
        sa.Column("estimated_due_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("recorded_by", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.LargeBinary(), nullable=False),
        sa.Column("parent_version", sa.Integer(), nullable=False),
        sa.Column("revision_sequence", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", sa.Uuid()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
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
            ["supersedes_id", "episode_id"],
            ["pregnancy_dating_revisions.id", "pregnancy_dating_revisions.episode_id"],
            name="fk_dating_revisions_supersedes_dating_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by"],
            ["users.id"],
            name="fk_dating_revisions_recorded_by_users",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("id", "episode_id", name="uq_dating_revision_id_episode"),
        sa.UniqueConstraint("id", "facility_id", name="uq_pregnancy_dating_revision_id_facility"),
        sa.UniqueConstraint(
            "episode_id",
            "patient_id",
            "revision_sequence",
            name="uq_pregnancy_dating_revision_episode_sequence",
        ),
        sa.UniqueConstraint(
            "facility_id",
            "idempotency_key",
            name="uq_pregnancy_dating_revision_facility_idempotency_key",
        ),
        sa.CheckConstraint(
            "char_length(trim(basis)) BETWEEN 1 AND 64",
            name="ck_pregnancy_dating_revision_basis_values",
        ),
        sa.CheckConstraint(
            "revision_sequence > 0", name="ck_pregnancy_dating_revision_sequence_positive"
        ),
        sa.CheckConstraint("version > 0", name="ck_pregnancy_dating_revision_version_positive"),
    )
    for column in ("episode_id", "patient_id", "facility_id", "idempotency_key"):
        op.create_index(
            f"ix_pregnancy_dating_revisions_{column}", "pregnancy_dating_revisions", [column]
        )
    op.execute("ALTER TABLE pregnancy_dating_revisions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE pregnancy_dating_revisions FORCE ROW LEVEL SECURITY")
    scope = (
        "patient_id = tunza_current_uuid('tunza.patient_id') "
        "AND facility_id = tunza_current_uuid('tunza.facility_id') "
        "AND tunza_pregnancy_assigned(episode_id)"
    )
    op.execute(
        f"CREATE POLICY pregnancy_dating_revisions_patient_scope ON pregnancy_dating_revisions "
        f"USING ({scope}) WITH CHECK ({scope} "
        "AND recorded_by = tunza_current_uuid('tunza.actor_id'))"
    )
    op.execute("GRANT SELECT, INSERT ON pregnancy_dating_revisions TO tunza_runtime")
    op.execute("""
        CREATE FUNCTION pregnancy_dating_revisions_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'pregnancy_dating_revisions rows are append-only';
        END $$
    """)
    op.execute(
        "CREATE TRIGGER pregnancy_dating_revisions_append_only BEFORE UPDATE OR DELETE "
        "ON pregnancy_dating_revisions FOR EACH ROW "
        "EXECUTE FUNCTION pregnancy_dating_revisions_append_only()"
    )


def downgrade() -> None:
    op.drop_table("pregnancy_dating_revisions")
    op.execute("DROP FUNCTION pregnancy_dating_revisions_append_only()")
    for name in ("read", "insert", "update"):
        op.execute(f"DROP POLICY pregnancy_episodes_{name} ON pregnancy_episodes")
    op.execute("DROP FUNCTION tunza_pregnancy_assigned(uuid)")
    op.execute("REVOKE UPDATE (version, updated_at) ON pregnancy_episodes FROM tunza_runtime")
    op.execute("""
        CREATE POLICY pregnancy_episodes_runtime_scope ON pregnancy_episodes
        USING (tunza_current_uuid('tunza.actor_id') IS NOT NULL AND EXISTS (
            SELECT 1 FROM episode_facility_assignments a WHERE a.episode_id = pregnancy_episodes.id
            AND a.facility_id = tunza_current_uuid('tunza.facility_id')))
        WITH CHECK (tunza_current_uuid('tunza.actor_id') IS NOT NULL
                    AND tunza_current_uuid('tunza.facility_id') IS NOT NULL)
    """)
    op.drop_index("uq_pregnancy_episode_ongoing_patient", table_name="pregnancy_episodes")
    op.drop_constraint(
        "uq_pregnancy_episode_facility_idempotency_key", "pregnancy_episodes", type_="unique"
    )
    op.drop_index("ix_pregnancy_episodes_idempotency_key", table_name="pregnancy_episodes")
    op.drop_index("ix_pregnancy_episodes_facility_id", table_name="pregnancy_episodes")
    for column in ("created_by", "request_hash", "idempotency_key", "facility_id"):
        op.drop_column("pregnancy_episodes", column)
