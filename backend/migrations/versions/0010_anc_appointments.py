"""Synthetic ANC appointments, immutable history and durable replay.

Revision ID: 0010_anc_appointments
Revises: 0009_pregnancy_dating
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_anc_appointments"
down_revision = "0009_pregnancy_dating"
branch_labels = None
depends_on = None


def _scope_columns():
    return [
        sa.Column(name, sa.Uuid(), nullable=False)
        for name in ("episode_id", "patient_id", "facility_id")
    ]


def _appointment_fk(name):
    return sa.ForeignKeyConstraint(
        ["appointment_id", "episode_id", "patient_id", "facility_id"],
        [
            "appointments.id",
            "appointments.episode_id",
            "appointments.patient_id",
            "appointments.facility_id",
        ],
        name=name,
        ondelete="RESTRICT",
    )


def upgrade():
    # Additive migration: existing patient, consent and dating records are untouched.
    op.create_table(
        "appointments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_scope_columns(),
        sa.Column("episode_facility_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("confirmed_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("attendance_effective_at", sa.DateTime(timezone=True)),
        sa.Column("supersedes_appointment_id", sa.Uuid()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["episode_id", "patient_id"],
            ["pregnancy_episodes.id", "pregnancy_episodes.patient_id"],
            name="fk_appointments_episode_patient",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["episode_facility_assignment_id", "episode_id", "facility_id"],
            [
                "episode_facility_assignments.id",
                "episode_facility_assignments.episode_id",
                "episode_facility_assignments.facility_id",
            ],
            name="fk_appointments_responsibility",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "id", "episode_id", "patient_id", "facility_id", name="uq_appointments_scope"
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_appointment_id", "episode_id", "patient_id", "facility_id"],
            [
                "appointments.id",
                "appointments.episode_id",
                "appointments.patient_id",
                "appointments.facility_id",
            ],
            name="fk_appointments_predecessor_scope",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("supersedes_appointment_id", name="uq_appointments_one_replacement"),
        sa.CheckConstraint(
            "status IN ('scheduled','confirmed','attended','missed','cancelled','rescheduled')",
            name="appointment_status",
        ),
        sa.CheckConstraint("version > 0", name="appointment_version"),
        sa.CheckConstraint(
            "(confirmed_by IS NULL) = (confirmed_at IS NULL)", name="confirmation_pair"
        ),
        sa.CheckConstraint(
            "status NOT IN ('confirmed','attended','missed') OR confirmed_by IS NOT NULL",
            name="confirmation_required",
        ),
        sa.CheckConstraint(
            "(status IN ('attended','missed')) = (attendance_effective_at IS NOT NULL)",
            name="attendance_required",
        ),
    )
    op.create_index("ix_appointments_episode_id", "appointments", ["episode_id"])
    op.create_index(
        "ix_appointments_facility_time_status",
        "appointments",
        ["facility_id", "starts_at", "status"],
    )
    op.create_index(
        "uq_appointments_active_slot",
        "appointments",
        ["episode_facility_assignment_id", "starts_at"],
        unique=True,
        postgresql_where=sa.text("status IN ('scheduled','confirmed')"),
    )
    op.create_table(
        "appointment_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("appointment_id", sa.Uuid(), nullable=False),
        *_scope_columns(),
        sa.Column("event_type", sa.String(16), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_data_cipher", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.String(64), nullable=False),
        sa.Column(
            "recorded_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _appointment_fk("fk_appointment_events_scope"),
        sa.UniqueConstraint("appointment_id", "sequence", name="uq_appointment_event_sequence"),
        sa.CheckConstraint("sequence > 0", name="event_sequence"),
        sa.CheckConstraint(
            "event_type IN ('scheduled','confirmed','attended','missed','cancelled','rescheduled')",
            name="event_type",
        ),
    )
    op.create_index(
        "ix_appointment_events_appointment_id", "appointment_events", ["appointment_id"]
    )
    op.create_table(
        "appointment_idempotency",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "actor_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("appointment_id", sa.Uuid(), nullable=False),
        *_scope_columns(),
        sa.Column("request_hash", sa.LargeBinary(), nullable=False),
        sa.Column("response_cipher", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.String(64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _appointment_fk("fk_appointment_command_scope"),
        sa.UniqueConstraint("actor_id", "operation", "key", name="uq_appointment_command_key"),
        sa.CheckConstraint("response_status IN (200,201)", name="command_response_status"),
    )
    op.execute("""
        CREATE FUNCTION tunza_appointment_scope(a uuid, e uuid, p uuid, f uuid) RETURNS boolean
        LANGUAGE sql STABLE SECURITY INVOKER SET search_path = public, pg_catalog AS $$
            SELECT f = tunza_current_uuid('tunza.facility_id')
              AND tunza_pregnancy_assigned(e)
              AND EXISTS (SELECT 1 FROM episode_facility_assignments r
                  WHERE r.id = a AND r.episode_id = e AND r.patient_id = p AND r.facility_id = f
                  AND r.valid_from <= statement_timestamp()
                  AND (r.valid_to IS NULL OR r.valid_to > statement_timestamp()))
        $$
    """)
    op.execute("REVOKE ALL ON FUNCTION tunza_appointment_scope(uuid,uuid,uuid,uuid) FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION tunza_appointment_scope(uuid,uuid,uuid,uuid) TO tunza_runtime"
    )
    scope = (
        "tunza_appointment_scope(episode_facility_assignment_id,episode_id,patient_id,facility_id)"
    )
    for table in ("appointments", "appointment_events", "appointment_idempotency"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY appointments_read ON appointments FOR SELECT USING ({scope} "
        "AND (tunza_current_uuid('tunza.patient_id') IS NULL "
        "OR patient_id = tunza_current_uuid('tunza.patient_id')))"
    )
    write_scope = scope + " AND patient_id = tunza_current_uuid('tunza.patient_id')"
    op.execute(
        f"CREATE POLICY appointments_insert ON appointments FOR INSERT WITH CHECK ({write_scope} "
        "AND created_by = tunza_current_uuid('tunza.actor_id'))"
    )
    op.execute(
        f"CREATE POLICY appointments_update ON appointments FOR UPDATE USING ({write_scope}) "
        f"WITH CHECK ({write_scope})"
    )
    for table, actor in (
        ("appointment_events", "recorded_by"),
        ("appointment_idempotency", "actor_id"),
    ):
        child_scope = (
            f"patient_id = tunza_current_uuid('tunza.patient_id') "
            f"AND facility_id = tunza_current_uuid('tunza.facility_id') "
            f"AND EXISTS (SELECT 1 FROM appointments a WHERE a.id = {table}.appointment_id)"
        )
        if table == "appointment_idempotency":
            child_scope += " AND actor_id = tunza_current_uuid('tunza.actor_id')"
        op.execute(
            f"CREATE POLICY {table}_scope ON {table} USING ({child_scope}) "
            f"WITH CHECK ({child_scope} AND {actor} = tunza_current_uuid('tunza.actor_id'))"
        )
    op.execute(
        "GRANT SELECT, INSERT ON appointments, appointment_events, "
        "appointment_idempotency TO tunza_runtime"
    )
    op.execute(
        "GRANT UPDATE (status, confirmed_by, confirmed_at, attendance_effective_at, "
        "version, updated_at) "
        "ON appointments TO tunza_runtime"
    )
    op.execute("""
        CREATE FUNCTION tunza_appointment_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Appointment history and command records are append-only'; END $$
    """)
    for table in ("appointment_events", "appointment_idempotency"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION tunza_appointment_immutable()"
        )
    op.execute("""
        CREATE FUNCTION tunza_appointment_transition() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'scheduled' OR NEW.version <> 1
                   OR NEW.confirmed_by IS NOT NULL OR NEW.attendance_effective_at IS NOT NULL
                THEN RAISE EXCEPTION 'Appointments begin as unconfirmed scheduled bookings'; END IF;
                IF NEW.supersedes_appointment_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM appointments a WHERE a.id = NEW.supersedes_appointment_id
                    AND a.status = 'rescheduled'
                ) THEN RAISE EXCEPTION 'A replacement requires a rescheduled predecessor'; END IF;
            ELSE
                IF (NEW.id, NEW.episode_id, NEW.patient_id, NEW.facility_id,
                    NEW.episode_facility_assignment_id, NEW.starts_at, NEW.created_by,
                    NEW.created_at, NEW.supersedes_appointment_id) IS DISTINCT FROM
                   (OLD.id, OLD.episode_id, OLD.patient_id, OLD.facility_id,
                    OLD.episode_facility_assignment_id, OLD.starts_at, OLD.created_by,
                    OLD.created_at, OLD.supersedes_appointment_id)
                THEN RAISE EXCEPTION 'Rescheduling requires a replacement appointment'; END IF;
                IF NEW.version <> OLD.version + 1 OR NOT (
                    (OLD.status = 'scheduled' AND
                        NEW.status IN ('confirmed','cancelled','rescheduled'))
                    OR (OLD.status = 'confirmed' AND
                        NEW.status IN ('attended','missed','cancelled','rescheduled'))
                ) THEN RAISE EXCEPTION 'Invalid appointment transition or version'; END IF;
                IF OLD.confirmed_by IS NOT NULL AND
                    (NEW.confirmed_by, NEW.confirmed_at) IS DISTINCT FROM
                    (OLD.confirmed_by, OLD.confirmed_at)
                THEN RAISE EXCEPTION 'Confirmation history is immutable'; END IF;
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER appointments_transition BEFORE INSERT OR UPDATE ON appointments "
        "FOR EACH ROW EXECUTE FUNCTION tunza_appointment_transition()"
    )
    op.execute("""
        CREATE FUNCTION tunza_appointment_history_check() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE current_status text; current_version integer;
        BEGIN
            SELECT status, version INTO current_status, current_version
                FROM appointments WHERE id = NEW.id;
            IF NOT EXISTS (SELECT 1 FROM appointment_events e WHERE e.appointment_id = NEW.id
                AND e.sequence = current_version AND e.event_type = current_status)
            THEN RAISE EXCEPTION 'Appointment changes require matching append-only history'; END IF;
            IF current_status = 'rescheduled' AND NOT EXISTS (
                SELECT 1 FROM appointments a WHERE a.supersedes_appointment_id = NEW.id)
            THEN RAISE EXCEPTION 'Rescheduling requires an atomic replacement'; END IF;
            RETURN NULL;
        END $$
    """)
    op.execute(
        "CREATE CONSTRAINT TRIGGER appointments_history_required "
        "AFTER INSERT OR UPDATE ON appointments DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION tunza_appointment_history_check()"
    )


def downgrade():
    for table in ("appointment_idempotency", "appointment_events", "appointments"):
        op.drop_table(table)
    op.execute("DROP FUNCTION tunza_appointment_transition()")
    op.execute("DROP FUNCTION tunza_appointment_history_check()")
    op.execute("DROP FUNCTION tunza_appointment_immutable()")
    op.execute("DROP FUNCTION tunza_appointment_scope(uuid,uuid,uuid,uuid)")
