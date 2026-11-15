"""Synthetic-only versioned content, independent review and publication controls.

Revision ID: 0014_clinical_content
Revises: 0013_pregnancy_completion
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0014_clinical_content"
down_revision = "0013_pregnancy_completion"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.create_table(
        "message_templates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("programme", sa.String(16), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("trigger_kind", sa.String(32), nullable=False),
        sa.Column("gestational_week", sa.Integer()),
        sa.Column(
            "created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("synthetic_only", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("programme = 'antenatal'", name="template_programme"),
        sa.CheckConstraint("code ~ '^SYN-[A-Z0-9][A-Z0-9_-]{2,59}$'", name="template_fixture_code"),
        sa.CheckConstraint(
            "(purpose = 'weekly_advice' AND trigger_kind = 'weekly' "
            "AND gestational_week IS NOT NULL AND gestational_week >= 0) "
            "OR (purpose = 'appointment_reminder' AND trigger_kind = 'appointment_reminder' "
            "AND gestational_week IS NULL)",
            name="template_trigger",
        ),
        sa.CheckConstraint("synthetic_only = true", name="template_fixture_only"),
        sa.CheckConstraint("version > 0", name="template_version"),
    )
    op.create_table(
        "template_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "template_id",
            sa.Uuid(),
            sa.ForeignKey("message_templates.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("language", sa.String(2), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "author_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("body_cipher", sa.LargeBinary(), nullable=False),
        sa.Column("allowed_parameters", JSONB, nullable=False),
        sa.Column("content_digest", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True)),
        sa.Column("effective_to", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "template_id", "language", "revision", name="uq_template_language_revision"
        ),
        sa.CheckConstraint("language IN ('en','lg')", name="content_language"),
        sa.CheckConstraint(
            "state IN ('draft','submitted','approved','rejected',"
            "'published','suspended','retired')",
            name="content_state",
        ),
        sa.CheckConstraint(
            "allowed_parameters = '[]'::jsonb", name="content_no_unapproved_parameters"
        ),
        sa.CheckConstraint("octet_length(content_digest) = 32", name="content_sha256"),
        sa.CheckConstraint("revision > 0 AND version > 0", name="content_versions"),
        sa.CheckConstraint(
            "(state IN ('published','suspended','retired')) = (effective_from IS NOT NULL)",
            name="content_publication_required",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="content_publication_interval",
        ),
    )
    op.create_index("ix_template_versions_template_id", "template_versions", ["template_id"])
    op.execute(
        "ALTER TABLE template_versions ADD CONSTRAINT ex_content_published_window "
        "EXCLUDE USING gist (template_id WITH =, language WITH =, "
        "tstzrange(effective_from,effective_to,'[)') WITH &&) WHERE (state = 'published')"
    )
    op.create_table(
        "content_reviews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "version_id",
            sa.Uuid(),
            sa.ForeignKey("template_versions.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "author_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "reviewer_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("decision", sa.String(8), nullable=False),
        sa.Column("content_digest", sa.LargeBinary(), nullable=False),
        sa.Column("reason_cipher", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.String(64), nullable=False),
        sa.Column("clinical_signoff", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("decision IN ('approve','reject')", name="content_review_decision"),
        sa.CheckConstraint("author_id <> reviewer_id", name="content_independent_reviewer"),
        sa.CheckConstraint("clinical_signoff = false", name="content_no_clinical_signoff"),
    )
    op.create_table(
        "content_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "version_id",
            sa.Uuid(),
            sa.ForeignKey("template_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column(
            "actor_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("event_data_cipher", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("version_id", "sequence", name="uq_content_event_sequence"),
        sa.CheckConstraint("sequence > 0", name="content_event_sequence"),
    )
    op.create_table(
        "content_idempotency",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "actor_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "template_id",
            sa.Uuid(),
            sa.ForeignKey("message_templates.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "version_id", sa.Uuid(), sa.ForeignKey("template_versions.id", ondelete="RESTRICT")
        ),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.LargeBinary(), nullable=False),
        sa.Column("response_cipher", sa.LargeBinary(), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("key_version", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("actor_id", "operation", "key", name="uq_content_command_key"),
    )
    op.execute("""
        CREATE FUNCTION public.tunza_content_permission(required text) RETURNS boolean
        LANGUAGE sql SECURITY INVOKER SET search_path = pg_catalog, public, pg_temp AS $$
            SELECT EXISTS (SELECT 1 FROM public.users u
                JOIN public.role_bindings b ON b.user_id = u.id
                JOIN public.roles r ON r.id = b.role_id AND r.scope_kind = b.scope_kind
                JOIN public.role_permissions rp ON rp.role_id = r.id
                JOIN public.permissions p ON p.id = rp.permission_id
                WHERE u.id = public.tunza_current_uuid('tunza.actor_id') AND u.status = 'active'
                  AND b.scope_kind = 'programme' AND b.facility_id IS NULL AND p.code = required
                  AND b.starts_at <= clock_timestamp()
                  AND (b.ends_at IS NULL OR b.ends_at > clock_timestamp()))
        $$
    """)
    op.execute("REVOKE ALL ON FUNCTION public.tunza_content_permission(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.tunza_content_permission(text) TO tunza_runtime")
    for table in (
        "message_templates",
        "template_versions",
        "content_reviews",
        "content_events",
        "content_idempotency",
    ):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY content_templates_read ON message_templates FOR SELECT "
        "USING (tunza_content_permission('content.read'))"
    )
    op.execute(
        "CREATE POLICY content_templates_insert ON message_templates FOR INSERT "
        "WITH CHECK (tunza_content_permission('content.author') "
        "AND created_by = tunza_current_uuid('tunza.actor_id'))"
    )
    op.execute(
        "CREATE POLICY content_templates_update ON message_templates FOR UPDATE "
        "USING (tunza_content_permission('content.read')) "
        "WITH CHECK (tunza_content_permission('content.read'))"
    )
    visible = (
        "tunza_content_permission('content.read') "
        "AND (author_id = tunza_current_uuid('tunza.actor_id') "
        "OR (state <> 'draft' AND (tunza_content_permission('content.review') "
        "OR state IN ('published','suspended','retired'))))"
    )
    op.execute(
        f"CREATE POLICY content_versions_read ON template_versions FOR SELECT USING ({visible})"
    )
    op.execute(
        "CREATE POLICY content_versions_insert ON template_versions FOR INSERT WITH CHECK ("
        "tunza_content_permission('content.author') "
        "AND author_id = tunza_current_uuid('tunza.actor_id') "
        "AND EXISTS (SELECT 1 FROM message_templates t "
        "WHERE t.id = template_id AND t.created_by = author_id))"
    )
    op.execute(
        f"CREATE POLICY content_versions_update ON template_versions FOR UPDATE "
        f"USING ({visible}) WITH CHECK ({visible})"
    )
    for table, actor in (("content_reviews", "reviewer_id"), ("content_events", "actor_id")):
        scope = f"EXISTS (SELECT 1 FROM template_versions v WHERE v.id = {table}.version_id)"
        op.execute(
            f"CREATE POLICY {table}_scope ON {table} USING ({scope}) "
            f"WITH CHECK ({scope} AND {actor} = tunza_current_uuid('tunza.actor_id'))"
        )
    scope = (
        "actor_id = tunza_current_uuid('tunza.actor_id') "
        "AND tunza_content_permission('content.read') AND (version_id IS NULL "
        "OR EXISTS (SELECT 1 FROM template_versions v WHERE v.id = version_id))"
    )
    op.execute(
        f"CREATE POLICY content_commands_scope ON content_idempotency "
        f"USING ({scope}) WITH CHECK ({scope})"
    )
    op.execute(
        "GRANT SELECT, INSERT ON message_templates, template_versions, content_reviews, "
        "content_events, content_idempotency TO tunza_runtime"
    )
    op.execute("GRANT UPDATE (version) ON message_templates TO tunza_runtime")
    op.execute(
        "GRANT UPDATE (body_cipher,allowed_parameters,content_digest,key_version,state,"
        "effective_from,effective_to,version) ON template_versions TO tunza_runtime"
    )
    op.execute("""
        CREATE FUNCTION public.tunza_content_template_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (NEW.id,NEW.code,NEW.programme,NEW.purpose,NEW.trigger_kind,NEW.gestational_week,
                NEW.created_by,NEW.synthetic_only,NEW.created_at) IS DISTINCT FROM
               (OLD.id,OLD.code,OLD.programme,OLD.purpose,OLD.trigger_kind,OLD.gestational_week,
                OLD.created_by,OLD.synthetic_only,OLD.created_at) OR NEW.version <> OLD.version + 1
            THEN RAISE EXCEPTION 'Content template metadata is immutable'; END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER content_template_guard BEFORE UPDATE ON message_templates "
        "FOR EACH ROW EXECUTE FUNCTION public.tunza_content_template_guard()"
    )
    op.execute("""
        CREATE FUNCTION public.tunza_content_version_guard() RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.state <> 'draft' OR NEW.version <> 1
                   OR NOT public.tunza_content_permission('content.author')
                   OR NEW.author_id <> public.tunza_current_uuid('tunza.actor_id') THEN
                    RAISE EXCEPTION 'Only owned drafts may be created' USING ERRCODE = '42501';
                END IF;
                RETURN NEW;
            END IF;
            IF (NEW.id,NEW.template_id,NEW.language,NEW.revision,NEW.author_id,NEW.created_at)
               IS DISTINCT FROM
                 (OLD.id,OLD.template_id,OLD.language,OLD.revision,OLD.author_id,OLD.created_at)
               OR NEW.version <> OLD.version + 1 THEN
                RAISE EXCEPTION 'Immutable version identity'; END IF;
            IF OLD.state <> 'draft' AND
               (NEW.body_cipher,NEW.content_digest,NEW.allowed_parameters,NEW.key_version)
               IS DISTINCT FROM
               (OLD.body_cipher,OLD.content_digest,OLD.allowed_parameters,OLD.key_version)
            THEN RAISE EXCEPTION 'Submitted content is immutable; create a new version'; END IF;
            IF OLD.effective_from IS NOT NULL AND
               (NEW.effective_from,NEW.effective_to)
               IS DISTINCT FROM (OLD.effective_from,OLD.effective_to)
            THEN RAISE EXCEPTION 'Published periods are immutable; create a new version'; END IF;
            IF OLD.state = 'draft' AND NEW.state IN ('draft','submitted') THEN
                IF NEW.author_id <> public.tunza_current_uuid('tunza.actor_id') OR
                   NOT public.tunza_content_permission(CASE WHEN NEW.state = 'draft'
                       THEN 'content.author' ELSE 'content.submit' END)
                THEN RAISE EXCEPTION 'Only the author may edit or submit'
                    USING ERRCODE = '42501'; END IF;
            ELSIF OLD.state = 'submitted' AND NEW.state IN ('approved','rejected') THEN
                IF NOT public.tunza_content_permission('content.review') OR NOT EXISTS (
                    SELECT 1 FROM public.content_reviews r WHERE r.version_id = NEW.id
                      AND r.reviewer_id = public.tunza_current_uuid('tunza.actor_id')
                      AND r.author_id = NEW.author_id AND r.reviewer_id <> NEW.author_id
                      AND r.content_digest = NEW.content_digest AND r.decision =
                        CASE WHEN NEW.state = 'approved' THEN 'approve' ELSE 'reject' END)
                THEN RAISE EXCEPTION 'Independent exact-digest fixture review required'
                    USING ERRCODE = '42501'; END IF;
            ELSIF OLD.state IN ('approved','suspended') AND NEW.state = 'published' THEN
                IF NOT public.tunza_content_permission('content.publish') OR NOT EXISTS (
                    SELECT 1 FROM public.content_reviews r WHERE r.version_id = NEW.id
                      AND r.decision = 'approve' AND r.reviewer_id <> NEW.author_id
                      AND r.author_id = NEW.author_id AND r.content_digest = NEW.content_digest)
                THEN RAISE EXCEPTION 'Exact independently approved fixture required'
                    USING ERRCODE = '42501'; END IF;
            ELSIF (OLD.state = 'published' AND NEW.state IN ('suspended','retired'))
               OR (OLD.state = 'suspended' AND NEW.state = 'retired') THEN
                IF NOT public.tunza_content_permission('content.suspend') THEN
                    RAISE EXCEPTION 'Suspension authority required' USING ERRCODE = '42501'; END IF;
            ELSE RAISE EXCEPTION 'Invalid content lifecycle transition'; END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER content_version_guard BEFORE INSERT OR UPDATE ON template_versions "
        "FOR EACH ROW EXECUTE FUNCTION public.tunza_content_version_guard()"
    )
    op.execute("""
        CREATE FUNCTION public.tunza_content_review_guard() RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp AS $$
        DECLARE v public.template_versions%ROWTYPE;
        BEGIN
            SELECT * INTO v FROM public.template_versions WHERE id = NEW.version_id FOR UPDATE;
            IF NOT FOUND OR v.state <> 'submitted' OR NEW.author_id <> v.author_id
               OR NEW.reviewer_id = v.author_id
               OR NEW.reviewer_id <> public.tunza_current_uuid('tunza.actor_id')
               OR NEW.content_digest <> v.content_digest
               OR NOT public.tunza_content_permission('content.review') THEN
                RAISE EXCEPTION 'Independent exact-digest fixture review required'
                    USING ERRCODE = '42501'; END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER content_review_guard BEFORE INSERT ON content_reviews "
        "FOR EACH ROW EXECUTE FUNCTION public.tunza_content_review_guard()"
    )
    op.execute("""
        CREATE FUNCTION public.tunza_content_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Content reviews, events and replay are append-only'; END $$
    """)
    for table in ("content_reviews", "content_events", "content_idempotency"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION public.tunza_content_immutable()"
        )
    op.execute("""
        CREATE FUNCTION public.tunza_content_history_guard() RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM public.content_events e WHERE e.version_id = NEW.id
                AND e.sequence = NEW.version AND (e.event_type = NEW.state
                    OR (NEW.state = 'draft' AND e.event_type IN ('draft_created','draft_edited'))))
            THEN RAISE EXCEPTION 'Version changes require append-only history'; END IF;
            RETURN NULL;
        END $$
    """)
    op.execute(
        "CREATE CONSTRAINT TRIGGER content_history_guard "
        "AFTER INSERT OR UPDATE ON template_versions DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION public.tunza_content_history_guard()"
    )
    op.execute("""
        CREATE FUNCTION public.tunza_content_event_guard() RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
            IF NEW.actor_id <> public.tunza_current_uuid('tunza.actor_id') OR NOT EXISTS (
                SELECT 1 FROM public.template_versions v WHERE v.id = NEW.version_id
                  AND v.version = NEW.sequence AND (v.state = NEW.event_type OR
                    (v.state = 'draft' AND NEW.event_type IN ('draft_created','draft_edited'))))
            THEN RAISE EXCEPTION 'Invalid content history event'; END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER content_event_guard BEFORE INSERT ON content_events "
        "FOR EACH ROW EXECUTE FUNCTION public.tunza_content_event_guard()"
    )
    op.execute("""
        CREATE FUNCTION public.tunza_content_review_consistency() RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM public.template_versions v WHERE v.id = NEW.version_id
                AND v.content_digest = NEW.content_digest AND v.author_id = NEW.author_id
                AND ((NEW.decision = 'reject' AND v.state = 'rejected') OR
                    (NEW.decision = 'approve'
                        AND v.state IN ('approved','published','suspended','retired'))))
            THEN RAISE EXCEPTION 'Fixture review and version state must commit together'; END IF;
            RETURN NULL;
        END $$
    """)
    op.execute(
        "CREATE CONSTRAINT TRIGGER content_review_consistency AFTER INSERT ON content_reviews "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION public.tunza_content_review_consistency()"
    )


def downgrade():
    for table in (
        "content_idempotency",
        "content_events",
        "content_reviews",
        "template_versions",
        "message_templates",
    ):
        op.drop_table(table)
    for name in (
        "tunza_content_template_guard",
        "tunza_content_version_guard",
        "tunza_content_review_guard",
        "tunza_content_immutable",
        "tunza_content_history_guard",
        "tunza_content_event_guard",
        "tunza_content_review_consistency",
    ):
        op.execute(f"DROP FUNCTION public.{name}()")
    op.execute("DROP FUNCTION public.tunza_content_permission(text)")
