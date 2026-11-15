"""Opt-in synthetic transfers with bounded cross-facility acceptance.

Revision ID: 0011_controlled_transfers
Revises: 0010_anc_appointments
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_controlled_transfers"
down_revision = "0010_anc_appointments"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute(
        "ALTER TABLE episode_facility_assignments ADD CONSTRAINT "
        "ex_episode_responsibility_no_overlap EXCLUDE USING gist "
        "(episode_id WITH =, tstzrange(valid_from, valid_to, '[)') WITH &&)"
    )
    op.create_table(
        "facility_transfers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *[
            sa.Column(name, sa.Uuid(), nullable=False)
            for name in ("source_assignment_id", "episode_id", "patient_id", "source_facility_id")
        ],
        sa.Column(
            "destination_facility_id",
            sa.Uuid(),
            sa.ForeignKey("facilities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "requested_by",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("synthetic", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["source_assignment_id", "episode_id", "source_facility_id"],
            [
                "episode_facility_assignments.id",
                "episode_facility_assignments.episode_id",
                "episode_facility_assignments.facility_id",
            ],
            name="fk_transfer_source_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["episode_id", "patient_id"],
            ["pregnancy_episodes.id", "pregnancy_episodes.patient_id"],
            name="fk_transfer_episode_patient",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "source_facility_id <> destination_facility_id", name="transfer_distinct_facilities"
        ),
        sa.CheckConstraint(
            "state IN ('pending','accepted','rejected','cancelled')", name="transfer_state"
        ),
        sa.CheckConstraint("synthetic = true", name="transfer_synthetic_only"),
        sa.CheckConstraint("version > 0", name="transfer_version"),
    )
    op.create_index(
        "uq_transfer_pending_source",
        "facility_transfers",
        ["source_assignment_id"],
        unique=True,
        postgresql_where=sa.text("state = 'pending'"),
    )
    op.create_index(
        "ix_transfers_destination_state", "facility_transfers", ["destination_facility_id", "state"]
    )
    op.create_table(
        "transfer_handover_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "transfer_id",
            sa.Uuid(),
            sa.ForeignKey("facility_transfers.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "prepared_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("handover_cipher", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "transfer_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "transfer_id",
            sa.Uuid(),
            sa.ForeignKey("facility_transfers.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "facility_id",
            sa.Uuid(),
            sa.ForeignKey("facilities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "recorded_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column(
            "destination_assignment_id",
            sa.Uuid(),
            sa.ForeignKey("episode_facility_assignments.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "receiver_membership_id",
            sa.Uuid(),
            sa.ForeignKey("facility_memberships.id", ondelete="RESTRICT"),
        ),
        sa.Column("destination_reference", sa.String(200)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "outcome IN ('accepted','rejected','cancelled')", name="transfer_decision_outcome"
        ),
        sa.CheckConstraint(
            "(outcome = 'accepted') = (destination_assignment_id IS NOT NULL "
            "AND receiver_membership_id IS NOT NULL AND destination_reference IS NOT NULL)",
            name="transfer_acceptance_receipt",
        ),
    )
    op.create_table(
        "transfer_idempotency",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "actor_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "facility_id",
            sa.Uuid(),
            sa.ForeignKey("facilities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "transfer_id",
            sa.Uuid(),
            sa.ForeignKey("facility_transfers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.LargeBinary(), nullable=False),
        sa.Column("response_cipher", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.String(64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("actor_id", "operation", "key", name="uq_transfer_command_key"),
    )
    op.execute("""
        CREATE FUNCTION tunza_transfer_permission(f uuid, permission text,
            admin_only boolean DEFAULT false) RETURNS boolean LANGUAGE sql SECURITY INVOKER
        SET search_path = pg_catalog, public, pg_temp AS $$
            SELECT EXISTS (
                SELECT 1 FROM public.users u JOIN public.facility_memberships m ON m.user_id = u.id
                JOIN public.facilities facility ON facility.id = m.facility_id
                JOIN public.role_bindings b ON b.user_id = u.id AND b.facility_id = m.facility_id
                JOIN public.roles r ON r.id = b.role_id AND r.scope_kind = b.scope_kind
                JOIN public.role_permissions rp ON rp.role_id = r.id
                JOIN public.permissions p ON p.id = rp.permission_id
                WHERE u.id = public.tunza_current_uuid('tunza.actor_id') AND u.status = 'active'
                  AND m.facility_id = f AND m.status = 'active' AND facility.active
                  AND b.scope_kind = 'facility' AND p.code = permission
                  AND (NOT admin_only OR r.code = 'FA')
                  AND m.starts_at <= clock_timestamp()
                  AND (m.ends_at IS NULL OR m.ends_at > clock_timestamp())
                  AND b.starts_at <= clock_timestamp()
                  AND (b.ends_at IS NULL OR b.ends_at > clock_timestamp())
            )
        $$
    """)
    op.execute("""
        CREATE FUNCTION tunza_transfer_visible(s uuid, d uuid, e uuid, requester uuid)
        RETURNS boolean
        LANGUAGE sql SECURITY INVOKER SET search_path = pg_catalog, public, pg_temp AS $$
            SELECT current_setting('tunza.synthetic_transfers', true) = 'on' AND (
                (public.tunza_current_uuid('tunza.facility_id') = s AND (
                    public.tunza_transfer_permission(s,'transfer.cancel',true)
                    OR (public.tunza_transfer_permission(s,'transfer.initiate',false) AND
                        (requester = public.tunza_current_uuid('tunza.actor_id')
                         OR public.tunza_pregnancy_assigned(e)))
                )) OR (public.tunza_current_uuid('tunza.facility_id') = d
                    AND public.tunza_transfer_permission(d,'transfer.accept',true))
            )
        $$
    """)
    for signature in (
        "tunza_transfer_permission(uuid,text,boolean)",
        "tunza_transfer_visible(uuid,uuid,uuid,uuid)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO tunza_runtime")
    for table in (
        "facility_transfers",
        "transfer_handover_records",
        "transfer_decisions",
        "transfer_idempotency",
    ):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    visible = (
        "tunza_transfer_visible(source_facility_id,destination_facility_id,episode_id,requested_by)"
    )
    op.execute(f"CREATE POLICY transfers_read ON facility_transfers FOR SELECT USING ({visible})")
    op.execute("""
        CREATE POLICY transfers_insert ON facility_transfers FOR INSERT WITH CHECK (
            current_setting('tunza.synthetic_transfers',true) = 'on'
            AND source_facility_id = public.tunza_current_uuid('tunza.facility_id')
            AND requested_by = public.tunza_current_uuid('tunza.actor_id')
            AND public.tunza_transfer_permission(source_facility_id,'transfer.initiate',false)
            AND public.tunza_pregnancy_assigned(episode_id)
            AND EXISTS (SELECT 1 FROM episode_facility_assignments a
                WHERE a.id = source_assignment_id AND a.patient_id = facility_transfers.patient_id
                AND a.valid_from <= statement_timestamp()
                AND (a.valid_to IS NULL OR a.valid_to > statement_timestamp()))
        )
    """)
    op.execute("""
        CREATE POLICY transfer_handover_scope ON transfer_handover_records USING (
            EXISTS (SELECT 1 FROM facility_transfers t WHERE t.id = transfer_id
                AND t.state IN ('pending','accepted'))
        ) WITH CHECK (prepared_by = public.tunza_current_uuid('tunza.actor_id')
            AND EXISTS (SELECT 1 FROM facility_transfers t WHERE t.id = transfer_id
                AND t.state = 'pending' AND t.requested_by = prepared_by
                AND t.source_facility_id = public.tunza_current_uuid('tunza.facility_id')))
    """)
    op.execute("""
        CREATE POLICY transfer_decision_scope ON transfer_decisions FOR SELECT USING (
            facility_id = public.tunza_current_uuid('tunza.facility_id')
            AND current_setting('tunza.synthetic_transfers',true) = 'on'
            AND (public.tunza_transfer_permission(facility_id,'transfer.accept',true)
                 OR (outcome = 'cancelled' AND
                    (public.tunza_transfer_permission(facility_id,'transfer.cancel',true)
                     OR (recorded_by = public.tunza_current_uuid('tunza.actor_id')
                         AND public.tunza_transfer_permission(
                             facility_id,'transfer.cancel',false)))))
        )
    """)
    op.execute("""
        CREATE POLICY transfer_command_scope ON transfer_idempotency USING (
            actor_id = public.tunza_current_uuid('tunza.actor_id')
            AND facility_id = public.tunza_current_uuid('tunza.facility_id')
            AND EXISTS (SELECT 1 FROM facility_transfers t WHERE t.id = transfer_id)
        ) WITH CHECK (actor_id = public.tunza_current_uuid('tunza.actor_id')
            AND facility_id = public.tunza_current_uuid('tunza.facility_id')
            AND EXISTS (SELECT 1 FROM facility_transfers t WHERE t.id = transfer_id))
    """)
    op.execute(
        "GRANT SELECT, INSERT ON facility_transfers, transfer_handover_records, "
        "transfer_idempotency TO tunza_runtime"
    )
    op.execute("GRANT SELECT ON transfer_decisions TO tunza_runtime")
    op.execute("""
        CREATE FUNCTION tunza_transfer_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Transfer history is append-only'; END $$
    """)
    for table in ("transfer_handover_records", "transfer_decisions", "transfer_idempotency"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION tunza_transfer_immutable()"
        )
    op.execute("""
        CREATE FUNCTION tunza_transfer_transition() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.state <> 'pending' OR NEW.version <> 1 THEN
                    RAISE EXCEPTION 'Transfers begin pending'; END IF;
            ELSE
                IF OLD.state <> 'pending' OR NEW.state NOT IN ('accepted','rejected','cancelled')
                   OR NEW.version <> OLD.version + 1
                   OR (NEW.id, NEW.source_assignment_id, NEW.episode_id, NEW.patient_id,
                       NEW.source_facility_id, NEW.destination_facility_id, NEW.requested_by,
                       NEW.created_at, NEW.synthetic) IS DISTINCT FROM
                      (OLD.id, OLD.source_assignment_id, OLD.episode_id, OLD.patient_id,
                       OLD.source_facility_id, OLD.destination_facility_id, OLD.requested_by,
                       OLD.created_at, OLD.synthetic)
                THEN RAISE EXCEPTION 'Invalid transfer transition'; END IF;
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER facility_transfer_transition BEFORE INSERT OR UPDATE ON facility_transfers "
        "FOR EACH ROW EXECUTE FUNCTION tunza_transfer_transition()"
    )
    # This is the sole cross-facility write capability. It cannot browse source
    # charts, accept actor/scope overrides, copy consent or activate communication.
    op.execute("""
        CREATE FUNCTION tunza_synthetic_transfer_decide(target uuid, expected integer, result text,
            reason text, receiver uuid, fixture_language text, empty_profile bytea,
            key_version text)
        RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp SET row_security = off AS $$
        DECLARE r public.facility_transfers%ROWTYPE;
            e public.pregnancy_episodes%ROWTYPE;
            source public.episode_facility_assignments%ROWTYPE;
            actor uuid := public.tunza_current_uuid('tunza.actor_id');
            scope uuid := public.tunza_current_uuid('tunza.facility_id');
            effective timestamptz; new_assignment uuid; decision_id uuid := gen_random_uuid();
            local_ref text; receiver_user uuid;
        BEGIN
            IF current_setting('tunza.synthetic_transfers',true) IS DISTINCT FROM 'on'
               OR actor IS NULL OR result NOT IN ('accepted','rejected','cancelled')
            THEN RAISE EXCEPTION 'Transfer unavailable' USING ERRCODE = '42501'; END IF;
            SELECT * INTO r FROM public.facility_transfers WHERE id = target;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'Transfer unavailable' USING ERRCODE = '42501'; END IF;
            IF (result IN ('accepted','rejected') AND
                (scope <> r.destination_facility_id OR NOT public.tunza_transfer_permission(
                    scope,'transfer.accept',true)))
               OR (result = 'cancelled' AND
                (scope <> r.source_facility_id OR NOT public.tunza_transfer_permission(
                    scope,'transfer.cancel',false)
                 OR NOT (public.tunza_transfer_permission(scope,'transfer.cancel',true)
                         OR public.tunza_pregnancy_assigned(r.episode_id))))
            THEN RAISE EXCEPTION 'Transfer unavailable' USING ERRCODE = '42501'; END IF;
            -- Global ordering agrees with appointments and dating: pregnancy first.
            SELECT * INTO e FROM public.pregnancy_episodes WHERE id = r.episode_id FOR UPDATE;
            SELECT * INTO r FROM public.facility_transfers WHERE id = target FOR UPDATE;
            IF r.version <> expected THEN
                RAISE EXCEPTION 'Stale transfer' USING ERRCODE = 'TZ412'; END IF;
            IF r.state <> 'pending' OR e.lifecycle <> 'ongoing' THEN
                RAISE EXCEPTION 'Transfer conflict' USING ERRCODE = 'TZ409'; END IF;
            IF reason IS NULL OR reason !~ '^SYN-[A-Z0-9][A-Z0-9-]{1,59}$' THEN
                RAISE EXCEPTION 'Synthetic reason code required' USING ERRCODE = 'TZ422'; END IF;
            SELECT * INTO source FROM public.episode_facility_assignments
                WHERE id = r.source_assignment_id FOR UPDATE;
            effective := clock_timestamp();
            IF source.valid_from >= effective OR source.valid_to IS NOT NULL THEN
                RAISE EXCEPTION 'Source responsibility changed' USING ERRCODE = 'TZ409'; END IF;
            IF NOT EXISTS (SELECT 1 FROM public.facility_patient_profiles p
                JOIN public.facilities f ON f.id = p.facility_id
                WHERE p.patient_id = r.patient_id AND p.facility_id = r.source_facility_id
                AND p.status = 'synthetic' AND f.active AND f.code ~ '^(SYN|TST)[A-Z0-9-]*$')
               OR NOT EXISTS (SELECT 1 FROM public.facilities f
                WHERE f.id = r.destination_facility_id
                AND f.active AND f.code ~ '^(SYN|TST)[A-Z0-9-]*$')
            THEN RAISE EXCEPTION 'Synthetic facilities and records required'
                USING ERRCODE = 'TZ422'; END IF;
            IF result IN ('accepted','rejected') THEN
                IF NOT public.tunza_transfer_permission(scope,'transfer.accept',true) THEN
                    RAISE EXCEPTION 'Transfer unavailable' USING ERRCODE = '42501'; END IF;
            ELSIF NOT public.tunza_transfer_permission(scope,'transfer.cancel',true)
                AND NOT EXISTS (
                SELECT 1 FROM public.participant_assignments p
                JOIN public.facility_memberships m ON m.id = p.membership_id
                    AND m.facility_id = p.facility_id
                WHERE p.episode_facility_assignment_id = source.id AND m.user_id = actor
                AND m.status = 'active' AND m.starts_at <= effective
                AND (m.ends_at IS NULL OR m.ends_at > effective)
                AND p.valid_from <= effective AND (p.valid_to IS NULL OR p.valid_to > effective)
                AND public.tunza_transfer_permission(scope,'transfer.cancel',false)
            ) THEN RAISE EXCEPTION 'Transfer unavailable' USING ERRCODE = '42501'; END IF;
            IF result = 'accepted' THEN
                IF fixture_language IS NULL OR fixture_language NOT IN ('en','lg')
                    OR empty_profile IS NULL OR key_version IS NULL THEN
                    RAISE EXCEPTION 'Synthetic destination setup required'
                        USING ERRCODE = 'TZ422'; END IF;
                SELECT m.user_id INTO receiver_user FROM public.facility_memberships m
                JOIN public.users u ON u.id = m.user_id
                WHERE m.id = receiver AND m.facility_id = scope
                  AND m.status = 'active' AND u.status = 'active'
                  AND m.starts_at <= effective AND (m.ends_at IS NULL OR m.ends_at > effective)
                  AND EXISTS (SELECT 1 FROM public.role_bindings b
                    JOIN public.roles role ON role.id = b.role_id
                    WHERE b.user_id = m.user_id AND b.facility_id = scope
                    AND b.scope_kind = 'facility'
                    AND role.code = 'HCW' AND role.scope_kind = b.scope_kind
                    AND NOT EXISTS (
                        SELECT 1 FROM (VALUES ('patient.read'),('pregnancy.read')) required(code)
                        WHERE NOT EXISTS (SELECT 1 FROM public.role_permissions rp
                            JOIN public.permissions perm ON perm.id = rp.permission_id
                            WHERE rp.role_id = role.id AND perm.code = required.code))
                    AND b.starts_at <= effective AND (b.ends_at IS NULL OR b.ends_at > effective));
                IF receiver_user IS NULL THEN RAISE EXCEPTION 'Active destination HCW required'
                    USING ERRCODE = 'TZ422'; END IF;
                local_ref := 'SYN-ADULT-TRANSFER-' ||
                    upper(replace(gen_random_uuid()::text,'-',''));
                INSERT INTO public.facility_patient_profiles
                    (id,patient_id,facility_id,local_reference,demographics_cipher,key_version,status)
                    VALUES (gen_random_uuid(),r.patient_id,scope,local_ref,
                            empty_profile,key_version,'synthetic')
                    ON CONFLICT (patient_id,facility_id) DO NOTHING;
                SELECT local_reference INTO local_ref FROM public.facility_patient_profiles
                    WHERE patient_id = r.patient_id AND facility_id = scope
                      AND status = 'synthetic';
                IF local_ref IS NULL THEN RAISE EXCEPTION 'Synthetic profile required'
                    USING ERRCODE = 'TZ422'; END IF;
                INSERT INTO public.communication_preferences (id,patient_id,facility_id,language)
                    VALUES (gen_random_uuid(),r.patient_id,scope,fixture_language)
                    ON CONFLICT (patient_id,facility_id) DO NOTHING;
                UPDATE public.participant_assignments
                    SET valid_to = effective, version = version + 1,
                    updated_at = effective WHERE episode_facility_assignment_id = source.id
                    AND valid_from < effective AND (valid_to IS NULL OR valid_to > effective);
                UPDATE public.episode_facility_assignments SET valid_to = effective,
                    version = version + 1, updated_at = effective WHERE id = source.id;
                new_assignment := gen_random_uuid();
                INSERT INTO public.episode_facility_assignments
                    (id,episode_id,patient_id,facility_id,valid_from)
                    VALUES (new_assignment,r.episode_id,r.patient_id,scope,effective);
                INSERT INTO public.participant_assignments
                    (id,episode_facility_assignment_id,facility_id,membership_id,duty,valid_from)
                    VALUES (gen_random_uuid(),new_assignment,scope,receiver,'primary',effective);
                UPDATE public.pregnancy_episodes SET communication_state = CASE
                    WHEN communication_state = 'withdrawn' THEN 'withdrawn' ELSE 'paused' END
                    WHERE id = r.episode_id;
            END IF;
            UPDATE public.pregnancy_episodes SET version = version + 1, updated_at = effective
                WHERE id = r.episode_id;
            INSERT INTO public.transfer_decisions
                (id,transfer_id,facility_id,recorded_by,outcome,reason_code,
                destination_assignment_id,receiver_membership_id,destination_reference)
                VALUES (decision_id,target,scope,actor,result,reason,new_assignment,
                    CASE WHEN result = 'accepted' THEN receiver ELSE NULL END,
                    CASE WHEN result = 'accepted' THEN local_ref ELSE NULL END);
            UPDATE public.facility_transfers SET state = result, version = version + 1,
                updated_at = effective WHERE id = target;
            RETURN decision_id;
        END $$
    """)
    signature = "tunza_synthetic_transfer_decide(uuid,integer,text,text,uuid,text,bytea,text)"
    op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO tunza_runtime")
    op.execute("""
        CREATE FUNCTION tunza_transfer_history_check() RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
            IF NEW.state = 'pending' THEN
                IF NOT EXISTS (SELECT 1 FROM public.transfer_handover_records h
                    WHERE h.transfer_id = NEW.id)
                THEN RAISE EXCEPTION 'Transfer requires immutable synthetic handover'; END IF;
            ELSIF NOT EXISTS (SELECT 1 FROM public.transfer_decisions d
                WHERE d.transfer_id = NEW.id AND d.outcome = NEW.state)
            THEN RAISE EXCEPTION 'Transfer requires a recorded decision'; END IF;
            RETURN NULL;
        END $$
    """)
    op.execute(
        "CREATE CONSTRAINT TRIGGER transfer_history_required "
        "AFTER INSERT OR UPDATE ON facility_transfers "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION tunza_transfer_history_check()"
    )


def downgrade():
    op.execute(
        "DROP FUNCTION tunza_synthetic_transfer_decide(uuid,integer,text,text,uuid,text,bytea,text)"
    )
    for table in (
        "transfer_idempotency",
        "transfer_decisions",
        "transfer_handover_records",
        "facility_transfers",
    ):
        op.drop_table(table)
    op.execute("DROP FUNCTION tunza_transfer_history_check()")
    op.execute("DROP FUNCTION tunza_transfer_transition()")
    op.execute("DROP FUNCTION tunza_transfer_immutable()")
    op.execute("DROP FUNCTION tunza_transfer_visible(uuid,uuid,uuid,uuid)")
    op.execute("DROP FUNCTION tunza_transfer_permission(uuid,text,boolean)")
    op.execute(
        "ALTER TABLE episode_facility_assignments "
        "DROP CONSTRAINT ex_episode_responsibility_no_overlap"
    )
