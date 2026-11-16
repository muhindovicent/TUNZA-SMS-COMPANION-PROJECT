"""Durable held candidates, safe operations metadata and synthetic capacity controls.

Revision ID: 0016_durable_scheduling
Revises: 0015_content_counter_guard
"""

from uuid import UUID, uuid5

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0016_durable_scheduling"
down_revision = "0015_content_counter_guard"
branch_labels = None
depends_on = None

REASON_CODES = (
    '["D05_unconfigured","D06_unconfigured","D07_unconfigured","D08_unconfigured",'
    '"D14_unconfigured","D14a_unconfigured","D15_unconfigured",'
    '"contact_safety_unconfigured","consent_policy_unconfigured","policy_missing",'
    '"global_paused","facility_paused","budget_missing","language_unavailable",'
    '"consent_absent","consent_withdrawn","consent_unapproved",'
    '"adult_eligibility_unverified","adult_eligibility_unapproved","episode_completed",'
    '"communication_paused","communication_completed","communication_withdrawn",'
    '"assignment_changed","dating_missing","appointment_changed","appointment_expired",'
    '"content_fixture_only","content_unavailable","expired"]'
)


def identity():
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def col(name, type_, nullable=False):
    return sa.Column(name, type_, nullable=nullable)


def fk(name, target, nullable=False, **kwargs):
    return sa.Column(
        name, sa.Uuid(), sa.ForeignKey(target, ondelete="RESTRICT"), nullable=nullable, **kwargs
    )


def upgrade():
    permission_id = uuid5(UUID("59f65c39-e04c-4cb8-b60e-52794b07c4f7"), "message.plan")
    op.execute(
        sa.text(
            "INSERT INTO permissions (id,code,description) VALUES "
            "(:id,'message.plan','Plan and recheck held synthetic candidates')"
        ).bindparams(id=permission_id)
    )
    op.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id,permission_id) "
            "SELECT id,:permission FROM roles WHERE code = 'HCW'"
        ).bindparams(permission=permission_id)
    )
    op.create_table(
        "message_occurrences",
        *identity(),
        col("episode_id", sa.Uuid()),
        col("patient_id", sa.Uuid()),
        col("facility_id", sa.Uuid()),
        col("assignment_id", sa.Uuid()),
        col("programme", sa.String(16)),
        col("kind", sa.String(32)),
        col("gestational_week", sa.Integer(), True),
        col("appointment_id", sa.Uuid(), True),
        col("reminder_code", sa.String(64), True),
        fk("template_id", "message_templates.id", True),
        col("language", sa.String(2)),
        col("due_at", sa.DateTime(timezone=True)),
        col("expires_at", sa.DateTime(timezone=True)),
        col("state", sa.String(16)),
        col("hold_reasons", JSONB),
        col("version", sa.Integer()),
        col("checked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["episode_id", "patient_id"],
            ["pregnancy_episodes.id", "pregnancy_episodes.patient_id"],
            name="fk_occurrence_episode",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assignment_id", "episode_id", "facility_id"],
            [
                "episode_facility_assignments.id",
                "episode_facility_assignments.episode_id",
                "episode_facility_assignments.facility_id",
            ],
            name="fk_occurrence_assignment",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["appointment_id", "episode_id", "patient_id", "facility_id"],
            [
                "appointments.id",
                "appointments.episode_id",
                "appointments.patient_id",
                "appointments.facility_id",
            ],
            name="fk_occurrence_appointment",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("programme = 'antenatal'", name="occurrence_programme"),
        sa.CheckConstraint(
            "(kind = 'weekly' AND gestational_week >= 0 AND "
            "gestational_week IS NOT NULL AND appointment_id IS NULL AND reminder_code IS NULL) "
            "OR (kind = 'appointment_reminder' AND gestational_week IS NULL AND "
            "appointment_id IS NOT NULL AND reminder_code ~ '^SYN-[A-Z0-9-]{2,60}$')",
            name="occurrence_business_key",
        ),
        sa.CheckConstraint(
            "state IN ('held','suppressed','cancelled','expired')", name="occurrence_no_activation"
        ),
        sa.CheckConstraint("language IN ('en','lg')", name="occurrence_language"),
        sa.CheckConstraint("expires_at > due_at", name="occurrence_window"),
        sa.CheckConstraint(
            "version > 0 AND jsonb_typeof(hold_reasons) = 'array' "
            "AND jsonb_array_length(hold_reasons) > 0",
            name="occurrence_holds",
        ),
        sa.CheckConstraint(
            f"hold_reasons <@ '{REASON_CODES}'::jsonb AND hold_reasons ? 'D07_unconfigured'",
            name="occurrence_reason_codes",
        ),
    )
    op.create_index(
        "uq_occurrence_weekly",
        "message_occurrences",
        ["episode_id", "programme", "gestational_week"],
        unique=True,
        postgresql_where=sa.text("kind = 'weekly'"),
    )
    op.create_index(
        "uq_occurrence_reminder",
        "message_occurrences",
        ["appointment_id", "reminder_code"],
        unique=True,
        postgresql_where=sa.text("kind = 'appointment_reminder'"),
    )
    op.create_index("ix_occurrence_due", "message_occurrences", ["facility_id", "state", "due_at"])
    op.create_table(
        "message_dispatches",
        *identity(),
        fk("occurrence_id", "message_occurrences.id", unique=True),
        col("state", sa.String(16)),
        fk("template_version_id", "template_versions.id", True),
        col("publication_version", sa.Integer(), True),
        sa.CheckConstraint(
            "state IN ('held','suppressed','cancelled','expired')", name="dispatch_no_submission"
        ),
        sa.CheckConstraint(
            "(template_version_id IS NULL) = (publication_version IS NULL) "
            "AND (publication_version IS NULL OR publication_version > 0)",
            name="dispatch_fixture_pair",
        ),
    )
    op.create_table(
        "message_scheduling_events",
        *identity(),
        fk("occurrence_id", "message_occurrences.id"),
        col("sequence", sa.Integer()),
        col("event_type", sa.String(32)),
        col("state", sa.String(16)),
        col("hold_reasons", JSONB),
        fk("audit_event_id", "audit_events.id", unique=True),
        sa.UniqueConstraint("occurrence_id", "sequence", name="uq_scheduling_event_sequence"),
    )
    op.create_table(
        "message_operations",
        *identity(),
        fk("facility_id", "facilities.id"),
        col("kind", sa.String(32)),
        col("state", sa.String(16)),
        col("hold_reasons", JSONB),
        col("due_at", sa.DateTime(timezone=True)),
        col("expires_at", sa.DateTime(timezone=True)),
        col("version", sa.Integer()),
    )
    op.create_table(
        "message_operation_events",
        *identity(),
        fk("occurrence_id", "message_operations.id"),
        col("sequence", sa.Integer()),
        col("event_type", sa.String(32)),
        col("state", sa.String(16)),
        col("hold_reasons", JSONB),
        sa.UniqueConstraint("occurrence_id", "sequence", name="uq_operation_event_sequence"),
    )
    op.create_table(
        "messaging_policies",
        *identity(),
        fk("facility_id", "facilities.id", True),
        col("code", sa.String(64)),
        col("revision", sa.Integer()),
        col("paused", sa.Boolean()),
        col("synthetic_only", sa.Boolean()),
        col("config", JSONB),
        sa.CheckConstraint(
            "synthetic_only = true AND config = '{}'::jsonb", name="messaging_policy_fixture"
        ),
        sa.CheckConstraint(
            "code ~ '^SYN-[A-Z0-9-]{2,60}$' AND revision > 0", name="messaging_policy_code"
        ),
    )
    op.create_index(
        "uq_messaging_global_revision",
        "messaging_policies",
        ["revision"],
        unique=True,
        postgresql_where=sa.text("facility_id IS NULL"),
    )
    op.create_index(
        "uq_messaging_facility_revision",
        "messaging_policies",
        ["facility_id", "revision"],
        unique=True,
        postgresql_where=sa.text("facility_id IS NOT NULL"),
    )
    op.create_table(
        "messaging_budgets",
        *identity(),
        fk("facility_id", "facilities.id", True),
        col("period_start", sa.DateTime(timezone=True)),
        col("period_end", sa.DateTime(timezone=True)),
        col("currency", sa.String(3)),
        col("cap", sa.Numeric(14, 4)),
        col("unit_cost", sa.Numeric(14, 4)),
        col("synthetic_only", sa.Boolean()),
        sa.CheckConstraint(
            "synthetic_only = true AND currency = 'SYN'", name="messaging_budget_fixture"
        ),
        sa.CheckConstraint(
            "period_end > period_start AND cap >= 0 AND unit_cost > 0",
            name="messaging_budget_values",
        ),
    )
    op.create_table(
        "cost_reservations",
        *identity(),
        fk("budget_id", "messaging_budgets.id"),
        fk("audit_event_id", "audit_events.id"),
        fk("facility_id", "facilities.id"),
        fk("actor_id", "users.id"),
        col("key", sa.String(128)),
        col("segments", sa.Integer()),
        col("amount", sa.Numeric(14, 4)),
        col("synthetic_only", sa.Boolean()),
        sa.UniqueConstraint("budget_id", "actor_id", "key", name="uq_capacity_reservation"),
        sa.CheckConstraint(
            "synthetic_only = true AND segments > 0 AND amount > 0",
            name="capacity_reservation_values",
        ),
    )
    op.create_table(
        "message_commands",
        *identity(),
        fk("actor_id", "users.id"),
        fk("facility_id", "facilities.id", True),
        fk("episode_id", "pregnancy_episodes.id", True),
        col("operation", sa.String(64)),
        col("key", sa.String(128)),
        col("request_hash", sa.LargeBinary()),
        col("response_cipher", sa.LargeBinary()),
        col("key_version", sa.String(64)),
        col("response_status", sa.Integer()),
        sa.UniqueConstraint("actor_id", "operation", "key", name="uq_message_command"),
    )
    op.execute("""
        CREATE FUNCTION public.tunza_message_permission(required text, facility uuid)
        RETURNS boolean LANGUAGE sql SECURITY INVOKER
        SET search_path = pg_catalog, public, pg_temp AS $$
          SELECT EXISTS (SELECT 1 FROM public.users u
            JOIN public.role_bindings b ON b.user_id = u.id
            JOIN public.roles r ON r.id = b.role_id AND r.scope_kind = b.scope_kind
            JOIN public.role_permissions rp ON rp.role_id = r.id
            JOIN public.permissions p ON p.id = rp.permission_id
            WHERE u.id = public.tunza_current_uuid('tunza.actor_id') AND u.status = 'active'
              AND p.code = required AND b.starts_at <= clock_timestamp()
              AND (b.ends_at IS NULL OR b.ends_at > clock_timestamp())
              AND ((b.scope_kind = 'platform' AND b.facility_id IS NULL) OR
                (facility IS NOT NULL AND b.scope_kind = 'facility' AND b.facility_id = facility
                 AND EXISTS (SELECT 1 FROM public.facility_memberships m
                    JOIN public.facilities f ON f.id = m.facility_id AND f.active
                    WHERE m.user_id = u.id AND m.facility_id = facility AND m.status = 'active'
                      AND m.starts_at <= clock_timestamp()
                      AND (m.ends_at IS NULL OR m.ends_at > clock_timestamp())))))
        $$
    """)
    op.execute("""
        CREATE FUNCTION public.tunza_message_care(episode uuid, facility uuid, required text)
        RETURNS boolean LANGUAGE sql SECURITY INVOKER
        SET search_path = pg_catalog, public, pg_temp AS $$
          SELECT facility = public.tunza_current_uuid('tunza.facility_id')
            AND public.tunza_message_permission(required, facility)
            AND public.tunza_pregnancy_assigned(episode)
        $$
    """)
    for signature in ("tunza_message_permission(text,uuid)", "tunza_message_care(uuid,uuid,text)"):
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO tunza_runtime")
    for table in (
        "message_occurrences",
        "message_dispatches",
        "message_scheduling_events",
        "message_operations",
        "message_operation_events",
        "messaging_policies",
        "messaging_budgets",
        "cost_reservations",
        "message_commands",
    ):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        # FORCE RLS also applies to a non-BYPASSRLS owner. Only trusted owner
        # trigger execution gets maintenance access; runtime has no owner membership.
        op.execute(
            f"CREATE POLICY messaging_owner_maintenance ON {table} USING "
            f"(pg_has_role(current_user,(SELECT relowner FROM pg_class "
            f"WHERE oid='public.{table}'::regclass),'USAGE'))"
        )
        op.execute(f"GRANT SELECT ON {table} TO tunza_runtime")
    scope = "tunza_message_care(episode_id,facility_id,'message.read')"
    write = "tunza_message_care(episode_id,facility_id,'message.plan')"
    op.execute(f"CREATE POLICY occurrence_read ON message_occurrences FOR SELECT USING ({scope})")
    op.execute(
        f"CREATE POLICY occurrence_insert ON message_occurrences FOR INSERT WITH CHECK ({write})"
    )
    op.execute(
        f"CREATE POLICY occurrence_update ON message_occurrences FOR UPDATE "
        f"USING ({write}) WITH CHECK ({write})"
    )
    op.execute("GRANT INSERT ON message_occurrences, message_scheduling_events TO tunza_runtime")
    op.execute(
        "GRANT UPDATE (state,hold_reasons,version,checked_at,language) "
        "ON message_occurrences TO tunza_runtime"
    )
    for table in ("message_dispatches", "message_scheduling_events"):
        visible = f"EXISTS (SELECT 1 FROM message_occurrences o WHERE o.id = {table}.occurrence_id)"
        op.execute(f"CREATE POLICY {table}_read ON {table} FOR SELECT USING ({visible})")
        op.execute(
            f"CREATE POLICY {table}_write ON {table} FOR ALL USING ({visible}) "
            f"WITH CHECK ({visible} AND EXISTS (SELECT 1 FROM message_occurrences o "
            f"WHERE o.id = {table}.occurrence_id AND "
            "tunza_message_care(o.episode_id,o.facility_id,'message.plan')))"
        )
    op.execute(
        "GRANT UPDATE (template_version_id,publication_version) "
        "ON message_dispatches TO tunza_runtime"
    )
    op.execute(
        "CREATE POLICY operation_read ON message_operations FOR SELECT "
        "USING (tunza_message_permission('message.ops',facility_id))"
    )
    op.execute(
        "CREATE POLICY operation_history_read ON message_operation_events FOR SELECT "
        "USING (EXISTS (SELECT 1 FROM message_operations o WHERE o.id = occurrence_id))"
    )
    for table in ("messaging_policies", "messaging_budgets"):
        read = "tunza_message_permission('messaging.control',facility_id)"
        op.execute(
            f"CREATE POLICY {table}_read ON {table} FOR SELECT USING ({read} OR "
            "(facility_id IS NULL AND tunza_message_permission('message.read',"
            "tunza_current_uuid('tunza.facility_id'))) OR "
            "tunza_message_permission('message.read',facility_id))"
        )
        op.execute(f"CREATE POLICY {table}_insert ON {table} FOR INSERT WITH CHECK ({read})")
        op.execute(f"GRANT INSERT ON {table} TO tunza_runtime")
    op.execute(
        "CREATE POLICY capacity_read ON cost_reservations FOR SELECT "
        "USING (tunza_message_permission('messaging.control',facility_id))"
    )
    op.execute(
        "CREATE POLICY capacity_insert ON cost_reservations FOR INSERT WITH CHECK "
        "(actor_id = tunza_current_uuid('tunza.actor_id') AND "
        "tunza_message_permission('messaging.control',facility_id))"
    )
    op.execute("GRANT INSERT ON cost_reservations,message_commands TO tunza_runtime")
    op.execute(
        "CREATE POLICY message_command_scope ON message_commands USING "
        "(actor_id = tunza_current_uuid('tunza.actor_id') AND "
        "((episode_id IS NOT NULL AND tunza_message_care(episode_id,facility_id,'message.plan')) "
        "OR (episode_id IS NULL AND tunza_message_permission('messaging.control',facility_id))))"
    )
    op.execute("""
        CREATE FUNCTION public.tunza_occurrence_guard() RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
          IF TG_OP = 'UPDATE' THEN
            IF (NEW.id,NEW.episode_id,NEW.patient_id,NEW.facility_id,NEW.assignment_id,NEW.kind,
                NEW.programme,NEW.gestational_week,NEW.appointment_id,NEW.reminder_code,
                NEW.template_id,NEW.due_at,NEW.expires_at,NEW.created_at) IS DISTINCT FROM
               (OLD.id,OLD.episode_id,OLD.patient_id,OLD.facility_id,OLD.assignment_id,OLD.kind,
                OLD.programme,OLD.gestational_week,OLD.appointment_id,OLD.reminder_code,
                OLD.template_id,OLD.due_at,OLD.expires_at,OLD.created_at)
                OR NEW.version <> OLD.version + 1 OR OLD.state IN ('cancelled','expired')
            THEN RAISE EXCEPTION 'Immutable occurrence identity or terminal state'; END IF;
          ELSIF NEW.version <> 1 THEN RAISE EXCEPTION 'Invalid occurrence version'; END IF;
          IF NOT pg_has_role(current_user,
              (SELECT relowner FROM pg_class WHERE oid = TG_RELID),'USAGE')
             AND NOT public.tunza_message_care(NEW.episode_id,NEW.facility_id,'message.plan')
          THEN RAISE EXCEPTION 'Scheduling authority required' USING ERRCODE = '42501'; END IF;
          NEW.checked_at := clock_timestamp();
          RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER occurrence_guard BEFORE INSERT OR UPDATE ON message_occurrences "
        "FOR EACH ROW EXECUTE FUNCTION public.tunza_occurrence_guard()"
    )
    op.execute("""
        CREATE FUNCTION public.tunza_scheduling_history_guard() RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM public.message_scheduling_events e
              WHERE e.occurrence_id = NEW.id AND e.sequence = NEW.version
                AND e.state = NEW.state AND e.hold_reasons = NEW.hold_reasons)
          THEN RAISE EXCEPTION 'Scheduling changes require matching history'; END IF;
          RETURN NULL;
        END $$
    """)
    op.execute(
        "CREATE CONSTRAINT TRIGGER scheduling_history_guard AFTER INSERT OR UPDATE "
        "ON message_occurrences DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION public.tunza_scheduling_history_guard()"
    )
    op.execute("""
        CREATE FUNCTION public.tunza_scheduling_project() RETURNS trigger LANGUAGE plpgsql
        SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
        DECLARE o public.message_occurrences%ROWTYPE; safe jsonb;
        BEGIN
          IF TG_RELID <> 'public.message_scheduling_events'::regclass THEN
            RAISE EXCEPTION 'Scheduling trigger only'; END IF;
          SELECT * INTO o FROM public.message_occurrences WHERE id = NEW.occurrence_id;
          IF NOT FOUND OR NEW.sequence <> o.version OR NEW.state <> o.state
            OR NEW.hold_reasons <> o.hold_reasons OR NOT EXISTS (
              SELECT 1 FROM public.audit_events a WHERE a.id = NEW.audit_event_id
                AND a.resource_id = o.id AND a.resource_type = 'message_occurrence'
                AND a.outcome = 'success' AND a.metadata->>'state' = o.state
                AND a.action = 'message.' || NEW.event_type
                AND a.xmin = pg_current_xact_id()::text::xid
                AND ((NEW.event_type IN ('plan','evaluate') AND a.actor_id =
                    public.tunza_current_uuid('tunza.actor_id') AND a.permission = 'message.plan'
                    AND public.tunza_message_care(o.episode_id,o.facility_id,'message.plan'))
                  OR (NEW.event_type = 'invalidate'
                      AND a.service_actor = 'scheduling_invalidation')))
          THEN RAISE EXCEPTION 'Audited authorized scheduling event required'; END IF;
          SELECT jsonb_agg(DISTINCT CASE WHEN value ~ '^D[0-9]'
              OR value IN ('global_paused','facility_paused','expired','content_unavailable',
                          'content_fixture_only','policy_missing','budget_missing')
              THEN value ELSE 'eligibility_restricted' END) INTO safe
            FROM jsonb_array_elements_text(o.hold_reasons);
          INSERT INTO public.message_operations
            (id,facility_id,kind,state,hold_reasons,due_at,expires_at,version,created_at)
          VALUES (o.id,o.facility_id,o.kind,o.state,safe,o.due_at,o.expires_at,
                  o.version,o.created_at)
          ON CONFLICT (id) DO UPDATE SET state=EXCLUDED.state,hold_reasons=EXCLUDED.hold_reasons,
                                        version=EXCLUDED.version;
          INSERT INTO public.message_operation_events
            (id,occurrence_id,sequence,event_type,state,hold_reasons)
          VALUES (NEW.id,o.id,NEW.sequence,NEW.event_type,o.state,safe);
          INSERT INTO public.message_dispatches (id,occurrence_id,state)
          VALUES (gen_random_uuid(),o.id,o.state)
          ON CONFLICT (occurrence_id) DO UPDATE SET state=EXCLUDED.state,
            template_version_id=CASE WHEN NEW.event_type='invalidate' THEN NULL
              ELSE message_dispatches.template_version_id END,
            publication_version=CASE WHEN NEW.event_type='invalidate' THEN NULL
              ELSE message_dispatches.publication_version END;
          RETURN NEW;
        END $$
    """)
    op.execute("REVOKE ALL ON FUNCTION public.tunza_scheduling_project() FROM PUBLIC")
    op.execute("""
        CREATE FUNCTION public.tunza_scheduling_event_origin() RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
          IF NEW.event_type='invalidate' AND NOT pg_has_role(current_user,
             (SELECT relowner FROM pg_class WHERE oid=TG_RELID),'USAGE') THEN
            RAISE EXCEPTION 'Invalidation history is trigger-only'; END IF;
          RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER scheduling_event_origin BEFORE INSERT ON message_scheduling_events "
        "FOR EACH ROW EXECUTE FUNCTION public.tunza_scheduling_event_origin()"
    )
    op.execute(
        "CREATE TRIGGER scheduling_project AFTER INSERT ON message_scheduling_events "
        "FOR EACH ROW EXECUTE FUNCTION public.tunza_scheduling_project()"
    )
    op.execute("""
        CREATE FUNCTION public.tunza_messaging_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Messaging records are append-only'; END $$
    """)
    for table in (
        "message_scheduling_events",
        "message_operation_events",
        "messaging_policies",
        "messaging_budgets",
        "cost_reservations",
        "message_commands",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION public.tunza_messaging_immutable()"
        )
    op.execute("""
        CREATE FUNCTION public.tunza_messaging_control_lock() RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
          PERFORM pg_advisory_xact_lock(59623166001);
          IF NOT public.tunza_message_permission('messaging.control',NEW.facility_id)
          THEN RAISE EXCEPTION 'Messaging control authority required'
              USING ERRCODE = '42501'; END IF;
          RETURN NEW;
        END $$
    """)
    for table in ("messaging_policies", "messaging_budgets"):
        op.execute(
            f"CREATE TRIGGER {table}_control BEFORE INSERT ON {table} FOR EACH ROW "
            "EXECUTE FUNCTION public.tunza_messaging_control_lock()"
        )
    op.execute("""
        CREATE FUNCTION public.tunza_capacity_guard() RETURNS trigger LANGUAGE plpgsql
        SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
        DECLARE b public.messaging_budgets%ROWTYPE; used numeric;
        BEGIN
          IF TG_RELID <> 'public.cost_reservations'::regclass THEN
            RAISE EXCEPTION 'Capacity trigger only'; END IF;
          PERFORM pg_advisory_xact_lock(59623166001);
          SELECT * INTO b FROM public.messaging_budgets WHERE id = NEW.budget_id FOR UPDATE;
          IF NOT FOUND OR NOT public.tunza_message_permission('messaging.control',NEW.facility_id)
            OR NEW.actor_id IS DISTINCT FROM public.tunza_current_uuid('tunza.actor_id')
            OR (b.facility_id IS NOT NULL AND b.facility_id <> NEW.facility_id)
            OR NOT (b.period_start <= clock_timestamp() AND b.period_end > clock_timestamp())
            OR NEW.amount <> NEW.segments * b.unit_cost
            OR NOT EXISTS (SELECT 1 FROM public.audit_events a WHERE a.id=NEW.audit_event_id
              AND a.actor_id=NEW.actor_id AND a.facility_id=NEW.facility_id
              AND a.permission='messaging.control' AND a.action='messaging.capacity'
              AND a.resource_type='capacity_simulation' AND a.outcome='success'
              AND a.xmin=pg_current_xact_id()::text::xid)
          THEN RAISE EXCEPTION 'Invalid synthetic capacity reservation'; END IF;
          IF COALESCE((SELECT paused FROM public.messaging_policies WHERE facility_id IS NULL
                       ORDER BY revision DESC LIMIT 1),true)
            OR COALESCE((SELECT paused FROM public.messaging_policies
                         WHERE facility_id = NEW.facility_id ORDER BY revision DESC LIMIT 1),true)
          THEN RAISE EXCEPTION 'Messaging paused or policy missing'; END IF;
          SELECT COALESCE(sum(amount),0) INTO used FROM public.cost_reservations
            WHERE budget_id=b.id;
          IF used + NEW.amount > b.cap THEN RAISE EXCEPTION 'Synthetic budget exhausted'; END IF;
          RETURN NEW;
        END $$
    """)
    op.execute("REVOKE ALL ON FUNCTION public.tunza_capacity_guard() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER capacity_guard BEFORE INSERT ON cost_reservations "
        "FOR EACH ROW EXECUTE FUNCTION public.tunza_capacity_guard()"
    )
    op.execute("""
        CREATE FUNCTION public.tunza_capacity_complete() RETURNS trigger LANGUAGE plpgsql
        SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
          IF TG_RELID <> 'public.cost_reservations'::regclass THEN
            RAISE EXCEPTION 'Capacity trigger only'; END IF;
          IF NOT EXISTS (SELECT 1 FROM public.messaging_budgets b WHERE b.facility_id IS NULL
            AND b.period_start <= clock_timestamp() AND b.period_end > clock_timestamp())
            OR NOT EXISTS (SELECT 1 FROM public.messaging_budgets b
              WHERE b.facility_id=NEW.facility_id AND b.period_start <= clock_timestamp()
                AND b.period_end > clock_timestamp())
            OR EXISTS (SELECT 1 FROM public.messaging_budgets b
              WHERE (b.facility_id IS NULL OR b.facility_id=NEW.facility_id)
                AND b.period_start <= clock_timestamp() AND b.period_end > clock_timestamp()
                AND NOT EXISTS (SELECT 1 FROM public.cost_reservations r WHERE r.budget_id=b.id
                  AND r.actor_id=NEW.actor_id AND r.facility_id=NEW.facility_id AND r.key=NEW.key
                  AND r.audit_event_id=NEW.audit_event_id AND r.segments=NEW.segments))
          THEN RAISE EXCEPTION 'Every applicable budget must be reserved atomically'; END IF;
          RETURN NULL;
        END $$
    """)
    op.execute("REVOKE ALL ON FUNCTION public.tunza_capacity_complete() FROM PUBLIC")
    op.execute(
        "CREATE CONSTRAINT TRIGGER capacity_complete AFTER INSERT ON cost_reservations "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION public.tunza_capacity_complete()"
    )
    # Narrow content metadata lookup; never exposes body, digest or author/reviewer identities.
    op.execute("""
        CREATE FUNCTION public.tunza_scheduling_content(episode uuid, facility uuid,
            target uuid, lang text, requested_purpose text, week integer) RETURNS jsonb
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
        DECLARE result jsonb;
        BEGIN
          IF NOT public.tunza_message_care(episode,facility,'message.read') THEN
            RAISE EXCEPTION 'Assigned messaging access required' USING ERRCODE='42501'; END IF;
          SELECT jsonb_build_object('id',v.id,'generation',v.version) INTO result
          FROM public.template_versions v JOIN public.message_templates t ON t.id=v.template_id
          JOIN public.content_reviews r ON r.version_id=v.id
          WHERE t.id=target AND t.purpose=requested_purpose
            AND t.gestational_week IS NOT DISTINCT FROM week
            AND v.language=lang AND v.state='published' AND v.effective_from <= clock_timestamp()
            AND (v.effective_to IS NULL OR v.effective_to > clock_timestamp())
            AND r.decision='approve' AND r.content_digest=v.content_digest
            AND r.author_id=v.author_id AND r.reviewer_id<>v.author_id AND NOT r.clinical_signoff;
          RETURN result;
        END $$
    """)
    op.execute(
        "REVOKE ALL ON FUNCTION public.tunza_scheduling_content(uuid,uuid,uuid,text,text,integer) "
        "FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.tunza_scheduling_content"
        "(uuid,uuid,uuid,text,text,integer) "
        "TO tunza_runtime"
    )
    op.execute("""
        CREATE FUNCTION public.tunza_dispatch_preparation_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
        DECLARE o public.message_occurrences%ROWTYPE; selected jsonb;
        BEGIN
          IF TG_OP='UPDATE' AND NEW.occurrence_id<>OLD.occurrence_id THEN
            RAISE EXCEPTION 'Immutable preparation identity'; END IF;
          SELECT * INTO o FROM public.message_occurrences WHERE id=NEW.occurrence_id;
          IF NOT FOUND OR NEW.state<>o.state THEN
            RAISE EXCEPTION 'Preparation must match current occurrence'; END IF;
          IF NEW.template_version_id IS NOT NULL AND (TG_OP='INSERT' OR
            (NEW.template_version_id,NEW.publication_version) IS DISTINCT FROM
            (OLD.template_version_id,OLD.publication_version)) THEN
            selected:=public.tunza_scheduling_content(o.episode_id,o.facility_id,o.template_id,
              o.language,CASE WHEN o.kind='weekly' THEN 'weekly_advice'
                ELSE 'appointment_reminder' END,o.gestational_week);
            IF selected IS NULL OR (selected->>'id')::uuid<>NEW.template_version_id
              OR (selected->>'generation')::integer<>NEW.publication_version THEN
              RAISE EXCEPTION 'Published fixture reference required; no clinical sign-off'; END IF;
          END IF;
          RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER dispatch_preparation_guard BEFORE INSERT OR UPDATE "
        "ON message_dispatches FOR EACH ROW "
        "EXECUTE FUNCTION public.tunza_dispatch_preparation_guard()"
    )
    op.execute("""
        CREATE FUNCTION public.tunza_scheduling_invalidate() RETURNS trigger LANGUAGE plpgsql
        SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
        DECLARE o public.message_occurrences%ROWTYPE; reason text; next_state text; aid uuid;
        BEGIN
          IF TG_TABLE_NAME = 'pregnancy_episodes' THEN
            IF NEW.lifecycle='completed' THEN reason:='episode_completed'; next_state:='cancelled';
            ELSIF NEW.communication_state='withdrawn' THEN reason:='consent_withdrawn';
                next_state:='suppressed';
            ELSIF NEW.communication_state='paused' THEN reason:='communication_paused';
                next_state:='held'; ELSE RETURN NEW; END IF;
          ELSIF TG_TABLE_NAME = 'appointments' THEN
            IF NEW.status='confirmed' THEN RETURN NEW; END IF;
            reason:='appointment_changed'; next_state:='cancelled';
          ELSIF TG_TABLE_NAME = 'episode_facility_assignments' THEN
            IF NEW.valid_to IS NULL THEN RETURN NEW; END IF;
            reason:='assignment_changed'; next_state:='cancelled';
          ELSIF TG_TABLE_NAME = 'consent_events' THEN
            IF NEW.decision<>'withdraw' THEN RETURN NEW; END IF;
            reason:='consent_withdrawn'; next_state:='suppressed';
          ELSIF TG_TABLE_NAME = 'template_versions' THEN
            IF NEW.state='published' THEN RETURN NEW; END IF;
            reason:='content_unavailable'; next_state:='held';
          ELSIF TG_TABLE_NAME = 'messaging_policies' THEN
            IF NOT NEW.paused THEN RETURN NEW; END IF;
            reason:=CASE WHEN NEW.facility_id IS NULL THEN 'global_paused'
                         ELSE 'facility_paused' END;
            next_state:='held';
          ELSE RAISE EXCEPTION 'Invalid invalidation source'; END IF;
          FOR o IN SELECT x.* FROM public.message_occurrences x
            WHERE x.state NOT IN ('cancelled','expired')
            AND CASE TG_TABLE_NAME
              WHEN 'pregnancy_episodes' THEN x.episode_id=(to_jsonb(NEW)->>'id')::uuid
              WHEN 'appointments' THEN x.appointment_id=(to_jsonb(NEW)->>'id')::uuid
              WHEN 'episode_facility_assignments' THEN x.assignment_id=(to_jsonb(NEW)->>'id')::uuid
              WHEN 'consent_events' THEN x.patient_id=(to_jsonb(NEW)->>'patient_id')::uuid
                AND x.facility_id=(to_jsonb(NEW)->>'facility_id')::uuid
              WHEN 'template_versions' THEN EXISTS (SELECT 1 FROM public.message_dispatches d
                WHERE d.occurrence_id=x.id AND d.template_version_id=(to_jsonb(NEW)->>'id')::uuid)
              WHEN 'messaging_policies' THEN (to_jsonb(NEW)->>'facility_id') IS NULL
                OR x.facility_id=(to_jsonb(NEW)->>'facility_id')::uuid END
            ORDER BY x.id FOR UPDATE
          LOOP
            IF o.hold_reasons ? reason AND o.state=next_state THEN CONTINUE; END IF;
            UPDATE public.message_occurrences SET state=next_state,
              hold_reasons=CASE WHEN hold_reasons ? reason THEN hold_reasons
                ELSE hold_reasons || jsonb_build_array(reason) END, version=version+1 WHERE id=o.id
              RETURNING * INTO o;
            aid:=gen_random_uuid();
            INSERT INTO public.audit_events
              (id,service_actor,request_id,permission,action,resource_type,resource_id,outcome,metadata)
            VALUES (aid,'scheduling_invalidation',gen_random_uuid(),'message.invalidate',
              'message.invalidate','message_occurrence',o.id,'success',jsonb_build_object('state',o.state));
            INSERT INTO public.message_scheduling_events
              (id,occurrence_id,sequence,event_type,state,hold_reasons,audit_event_id)
            VALUES (gen_random_uuid(),o.id,o.version,'invalidate',o.state,o.hold_reasons,aid);
          END LOOP;
          RETURN NEW;
        END $$
    """)
    op.execute("REVOKE ALL ON FUNCTION public.tunza_scheduling_invalidate() FROM PUBLIC")
    for table, event in (
        ("pregnancy_episodes", "UPDATE OF lifecycle,communication_state"),
        ("appointments", "UPDATE OF status"),
        ("episode_facility_assignments", "UPDATE OF valid_to"),
        ("consent_events", "INSERT"),
        ("template_versions", "UPDATE OF state"),
        ("messaging_policies", "INSERT"),
    ):
        op.execute(
            f"CREATE TRIGGER scheduling_invalidate AFTER {event} ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION public.tunza_scheduling_invalidate()"
        )


def downgrade():
    for table in (
        "pregnancy_episodes",
        "appointments",
        "episode_facility_assignments",
        "consent_events",
        "template_versions",
        "messaging_policies",
    ):
        op.execute(f"DROP TRIGGER scheduling_invalidate ON {table}")
    for table in (
        "message_commands",
        "cost_reservations",
        "messaging_budgets",
        "messaging_policies",
        "message_operation_events",
        "message_operations",
        "message_scheduling_events",
        "message_dispatches",
        "message_occurrences",
    ):
        op.drop_table(table)
    for name, args in (
        ("tunza_scheduling_invalidate", ""),
        ("tunza_scheduling_project", ""),
        ("tunza_scheduling_event_origin", ""),
        ("tunza_occurrence_guard", ""),
        ("tunza_scheduling_history_guard", ""),
        ("tunza_messaging_immutable", ""),
        ("tunza_messaging_control_lock", ""),
        ("tunza_capacity_guard", ""),
        ("tunza_capacity_complete", ""),
        ("tunza_dispatch_preparation_guard", ""),
        ("tunza_scheduling_content", "uuid,uuid,uuid,text,text,integer"),
        ("tunza_message_care", "uuid,uuid,text"),
        ("tunza_message_permission", "text,uuid"),
    ):
        op.execute(f"DROP FUNCTION public.{name}({args})")
    op.execute(
        "DELETE FROM role_permissions WHERE permission_id IN "
        "(SELECT id FROM permissions WHERE code='message.plan')"
    )
    op.execute("DELETE FROM permissions WHERE code='message.plan'")
