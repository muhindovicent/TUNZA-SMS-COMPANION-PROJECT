"""Append-only synthetic pregnancy completion and lifecycle closure.

Revision ID: 0013_pregnancy_completion
Revises: 0012_fixture_execution_boundary
"""

import sqlalchemy as sa
from alembic import op

revision = "0013_pregnancy_completion"
down_revision = "0012_fixture_execution_boundary"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("pregnancy_episodes", sa.Column("completion_record_id", sa.Uuid()))
    op.create_table(
        "pregnancy_completion_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *[
            sa.Column(name, sa.Uuid(), nullable=False)
            for name in (
                "episode_id",
                "patient_id",
                "facility_id",
                "episode_facility_assignment_id",
            )
        ],
        sa.Column("completed_on", sa.Date(), nullable=False),
        *[
            sa.Column(name, sa.LargeBinary(), nullable=False)
            for name in (
                "approved_fields_cipher",
                "reason_cipher",
                "response_cipher",
                "request_hash",
            )
        ],
        sa.Column("key_version", sa.String(64), nullable=False),
        sa.Column(
            "recorded_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supersedes_id", sa.Uuid()),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("parent_version", sa.Integer(), nullable=False),
        sa.Column("cancelled_appointment_count", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
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
        sa.CheckConstraint("sequence > 0 AND parent_version > 1", name="completion_versions"),
        sa.CheckConstraint("cancelled_appointment_count >= 0", name="completion_cancel_count"),
    )
    op.create_index(
        "uq_completion_initial_episode",
        "pregnancy_completion_records",
        ["episode_id"],
        unique=True,
        postgresql_where=sa.text("supersedes_id IS NULL"),
    )
    op.create_foreign_key(
        "fk_episode_current_completion",
        "pregnancy_episodes",
        "pregnancy_completion_records",
        ["completion_record_id", "id"],
        ["id", "episode_id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    # Existing legacy completed rows may have no recorded completion; do not
    # fabricate dates/outcomes or alter existing history during an upgrade.
    op.create_check_constraint(
        op.f("ck_pregnancy_episodes_completion_state_alignment"),
        "pregnancy_episodes",
        "completion_record_id IS NULL OR (lifecycle = 'completed' "
        "AND communication_state IN ('completed','withdrawn'))",
    )
    op.add_column("appointment_events", sa.Column("completion_record_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_appointment_event_completion_scope",
        "appointment_events",
        "pregnancy_completion_records",
        ["completion_record_id", "episode_id", "patient_id", "facility_id"],
        ["id", "episode_id", "patient_id", "facility_id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_appointment_events_completion_event_cancelled"),
        "appointment_events",
        "completion_record_id IS NULL OR event_type = 'cancelled'",
    )
    op.execute("""
        CREATE FUNCTION public.tunza_pregnancy_can_complete(target uuid) RETURNS boolean
        LANGUAGE sql SECURITY INVOKER SET search_path = pg_catalog, public, pg_temp AS $$
            SELECT public.tunza_pregnancy_assigned(target)
              AND EXISTS (SELECT 1 FROM public.pregnancy_episodes e
                JOIN public.facility_patient_profiles p ON p.patient_id = e.patient_id
                WHERE e.id = target
                  AND p.facility_id = public.tunza_current_uuid('tunza.facility_id')
                  AND p.status = 'synthetic')
              AND EXISTS (SELECT 1 FROM public.users u
                JOIN public.facility_memberships m ON m.user_id = u.id
                JOIN public.role_bindings b ON b.user_id = u.id AND b.facility_id = m.facility_id
                JOIN public.roles r ON r.id = b.role_id AND r.scope_kind = b.scope_kind
                JOIN public.role_permissions rp ON rp.role_id = r.id
                JOIN public.permissions permission ON permission.id = rp.permission_id
                WHERE u.id = public.tunza_current_uuid('tunza.actor_id') AND u.status = 'active'
                  AND m.facility_id = public.tunza_current_uuid('tunza.facility_id')
                  AND m.status = 'active' AND b.scope_kind = 'facility'
                  AND permission.code = 'pregnancy.complete'
                  AND m.starts_at <= clock_timestamp()
                  AND (m.ends_at IS NULL OR m.ends_at > clock_timestamp())
                  AND b.starts_at <= clock_timestamp()
                  AND (b.ends_at IS NULL OR b.ends_at > clock_timestamp()))
        $$
    """)
    op.execute("REVOKE ALL ON FUNCTION public.tunza_pregnancy_can_complete(uuid) FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.tunza_pregnancy_can_complete(uuid) TO tunza_runtime"
    )
    op.execute("ALTER TABLE pregnancy_completion_records ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE pregnancy_completion_records FORCE ROW LEVEL SECURITY")
    scope = (
        "facility_id = tunza_current_uuid('tunza.facility_id') "
        "AND patient_id = tunza_current_uuid('tunza.patient_id') "
        "AND tunza_pregnancy_can_complete(episode_id)"
    )
    op.execute(
        f"CREATE POLICY completion_scope ON pregnancy_completion_records USING ({scope}) "
        f"WITH CHECK ({scope} AND recorded_by = tunza_current_uuid('tunza.actor_id'))"
    )
    op.execute("GRANT SELECT, INSERT ON pregnancy_completion_records TO tunza_runtime")
    op.execute(
        "GRANT UPDATE (lifecycle, communication_state, completion_record_id) "
        "ON pregnancy_episodes TO tunza_runtime"
    )
    op.execute("""
        CREATE FUNCTION public.tunza_completion_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Pregnancy completion records are append-only'; END $$
    """)
    op.execute(
        "CREATE TRIGGER completion_immutable BEFORE UPDATE OR DELETE "
        "ON pregnancy_completion_records "
        "FOR EACH ROW EXECUTE FUNCTION public.tunza_completion_immutable()"
    )
    op.execute("""
        CREATE FUNCTION public.tunza_completion_revision_guard() RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp AS $$
        DECLARE parent public.pregnancy_episodes%ROWTYPE;
            previous public.pregnancy_completion_records%ROWTYPE;
        BEGIN
            SELECT * INTO parent FROM public.pregnancy_episodes
                WHERE id = NEW.episode_id FOR UPDATE;
            IF NOT FOUND OR NOT public.tunza_pregnancy_can_complete(parent.id) THEN
                RAISE EXCEPTION 'Completion unavailable' USING ERRCODE = '42501'; END IF;
            IF NEW.parent_version <> parent.version + 1 THEN
                RAISE EXCEPTION 'Invalid completion parent version'; END IF;
            IF NEW.supersedes_id IS NULL THEN
                IF parent.lifecycle <> 'ongoing' OR parent.completion_record_id IS NOT NULL
                   OR NEW.sequence <> 1 OR NEW.closed_at <> NEW.recorded_at THEN
                    RAISE EXCEPTION 'Initial completion requires an ongoing episode'; END IF;
            ELSE
                SELECT * INTO previous FROM public.pregnancy_completion_records
                    WHERE id = NEW.supersedes_id;
                IF NOT FOUND OR parent.lifecycle <> 'completed'
                   OR parent.completion_record_id IS DISTINCT FROM previous.id
                   OR previous.episode_id <> NEW.episode_id
                   OR NEW.sequence <> previous.sequence + 1 OR NEW.closed_at <> previous.closed_at
                   OR NEW.cancelled_appointment_count <> 0 THEN
                    RAISE EXCEPTION 'Correction must supersede current record without reopening';
                END IF;
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER completion_revision_guard BEFORE INSERT ON pregnancy_completion_records "
        "FOR EACH ROW EXECUTE FUNCTION public.tunza_completion_revision_guard()"
    )
    op.execute("""
        CREATE FUNCTION public.tunza_episode_completion_guard() RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
            IF OLD.lifecycle = 'completed' AND NEW.lifecycle <> 'completed' THEN
                RAISE EXCEPTION 'Completed episodes cannot be reopened'; END IF;
            IF OLD.communication_state = 'withdrawn' AND NEW.communication_state <> 'withdrawn' THEN
                RAISE EXCEPTION 'Withdrawal cannot be reversed by lifecycle closure'; END IF;
            IF OLD.completion_record_id IS NOT NULL AND NEW.completion_record_id IS NULL THEN
                RAISE EXCEPTION 'Completion history pointer cannot be cleared'; END IF;
            IF NEW.completion_record_id IS NULL THEN
                -- Trusted owner fixtures/legacy records remain representable.
                -- Ordinary application logins cannot fabricate a closure without history.
                IF (NEW.lifecycle, NEW.communication_state) IS DISTINCT FROM
                   (OLD.lifecycle, OLD.communication_state) AND NOT pg_has_role(current_user,
                       (SELECT relowner FROM pg_class WHERE oid = TG_RELID), 'USAGE') THEN
                    RAISE EXCEPTION 'Lifecycle changes require completion history'; END IF;
            ELSIF (NEW.completion_record_id,NEW.version,NEW.communication_state,NEW.lifecycle)
                  IS DISTINCT FROM
                    (OLD.completion_record_id,OLD.version,OLD.communication_state,OLD.lifecycle)
            THEN
                IF NEW.completion_record_id IS NOT DISTINCT FROM OLD.completion_record_id
                   OR NEW.version <> OLD.version + 1
                   OR NOT public.tunza_pregnancy_can_complete(NEW.id) THEN
                    RAISE EXCEPTION 'Completed lifecycle changes require a new authorized revision';
                END IF;
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER pregnancy_completion_guard BEFORE UPDATE ON pregnancy_episodes "
        "FOR EACH ROW EXECUTE FUNCTION public.tunza_episode_completion_guard()"
    )
    op.execute("""
        CREATE FUNCTION public.tunza_completion_consistency() RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp AS $$
        DECLARE parent public.pregnancy_episodes%ROWTYPE;
        BEGIN
            SELECT * INTO parent FROM public.pregnancy_episodes WHERE id = NEW.episode_id;
            IF parent.completion_record_id IS DISTINCT FROM NEW.id
               OR parent.lifecycle <> 'completed'
               OR parent.version <> NEW.parent_version THEN
                RAISE EXCEPTION 'Completion record and current parent must commit together'; END IF;
            IF (SELECT count(*) FROM public.appointment_events e
                WHERE e.completion_record_id = NEW.id)
               <> NEW.cancelled_appointment_count THEN
                RAISE EXCEPTION 'Completion cancellation history is incomplete'; END IF;
            IF EXISTS (SELECT 1 FROM public.appointments a
                WHERE a.episode_facility_assignment_id = NEW.episode_facility_assignment_id
                  AND a.starts_at >= NEW.closed_at AND a.status IN ('scheduled','confirmed')) THEN
                RAISE EXCEPTION 'Completion must invalidate all future current ANC bookings';
            END IF;
            RETURN NULL;
        END $$
    """)
    op.execute(
        "CREATE CONSTRAINT TRIGGER completion_consistency "
        "AFTER INSERT ON pregnancy_completion_records DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION public.tunza_completion_consistency()"
    )
    op.execute("""
        CREATE FUNCTION public.tunza_completion_pointer_guard() RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
            IF NEW.completion_record_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM public.pregnancy_completion_records c
                WHERE c.id = NEW.completion_record_id AND c.episode_id = NEW.id
                  AND c.parent_version = NEW.version) THEN
                RAISE EXCEPTION 'Invalid current completion revision/version'; END IF;
            RETURN NULL;
        END $$
    """)
    op.execute(
        "CREATE CONSTRAINT TRIGGER completion_pointer_guard AFTER UPDATE ON pregnancy_episodes "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION public.tunza_completion_pointer_guard()"
    )
    op.execute("""
        CREATE FUNCTION public.tunza_ongoing_appointment_guard() RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp AS $$
        DECLARE state text;
        BEGIN
            IF TG_OP = 'INSERT' OR NEW.status IN ('scheduled','confirmed') THEN
                SELECT lifecycle INTO state FROM public.pregnancy_episodes
                    WHERE id = NEW.episode_id FOR UPDATE;
                IF state IS NULL THEN
                    RAISE EXCEPTION 'Pregnancy unavailable' USING ERRCODE = '42501'; END IF;
                IF state IS DISTINCT FROM 'ongoing' THEN
                    RAISE EXCEPTION 'New bookings and confirmation require an ongoing pregnancy';
                END IF;
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER appointments_lifecycle_guard BEFORE INSERT OR UPDATE ON appointments "
        "FOR EACH ROW EXECUTE FUNCTION public.tunza_ongoing_appointment_guard()"
    )
    op.execute("""
        CREATE FUNCTION public.tunza_ongoing_dating_guard() RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp AS $$
        DECLARE state text;
        BEGIN
            SELECT lifecycle INTO state FROM public.pregnancy_episodes
                WHERE id = NEW.episode_id FOR UPDATE;
            IF state IS NULL THEN
                RAISE EXCEPTION 'Pregnancy unavailable' USING ERRCODE = '42501'; END IF;
            IF state IS DISTINCT FROM 'ongoing' THEN
                RAISE EXCEPTION 'Dating requires an ongoing pregnancy'; END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER dating_lifecycle_guard BEFORE INSERT ON pregnancy_dating_revisions "
        "FOR EACH ROW EXECUTE FUNCTION public.tunza_ongoing_dating_guard()"
    )


def downgrade():
    for trigger, table in (
        ("dating_lifecycle_guard", "pregnancy_dating_revisions"),
        ("appointments_lifecycle_guard", "appointments"),
        ("completion_pointer_guard", "pregnancy_episodes"),
        ("pregnancy_completion_guard", "pregnancy_episodes"),
    ):
        op.execute(f"DROP TRIGGER {trigger} ON {table}")
    op.execute(
        "REVOKE UPDATE (lifecycle, communication_state, completion_record_id) "
        "ON pregnancy_episodes FROM tunza_runtime"
    )
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tunza_fixture_runtime') THEN
            REVOKE UPDATE (lifecycle,communication_state) ON pregnancy_episodes
                FROM tunza_fixture_runtime;
        END IF; END $$""")
    op.drop_constraint(
        "fk_appointment_event_completion_scope", "appointment_events", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("ck_appointment_events_completion_event_cancelled"),
        "appointment_events",
        type_="check",
    )
    op.drop_column("appointment_events", "completion_record_id")
    op.drop_constraint("fk_episode_current_completion", "pregnancy_episodes", type_="foreignkey")
    op.drop_constraint(
        op.f("ck_pregnancy_episodes_completion_state_alignment"),
        "pregnancy_episodes",
        type_="check",
    )
    op.drop_column("pregnancy_episodes", "completion_record_id")
    op.drop_table("pregnancy_completion_records")
    for name in (
        "tunza_completion_immutable",
        "tunza_completion_revision_guard",
        "tunza_episode_completion_guard",
        "tunza_completion_consistency",
        "tunza_completion_pointer_guard",
        "tunza_ongoing_appointment_guard",
        "tunza_ongoing_dating_guard",
    ):
        op.execute(f"DROP FUNCTION public.{name}()")
    op.execute("DROP FUNCTION public.tunza_pregnancy_can_complete(uuid)")
