"""Derive template counter increments from authorized, audited content events.

Revision ID: 0015_content_counter_guard
Revises: 0014_clinical_content
"""

import sqlalchemy as sa
from alembic import op

revision = "0015_content_counter_guard"
down_revision = "0014_clinical_content"
branch_labels = None
depends_on = None


def upgrade():
    # Preserve old append-only history. Only new events require the audit link.
    op.add_column("content_events", sa.Column("audit_event_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_content_event_audit",
        "content_events",
        "audit_events",
        ["audit_event_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint("uq_content_event_audit", "content_events", ["audit_event_id"])
    op.execute("REVOKE UPDATE (version) ON message_templates FROM tunza_runtime")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tunza_fixture_runtime') THEN
            REVOKE UPDATE (version) ON message_templates FROM tunza_fixture_runtime;
        END IF; END $$""")
    op.execute("""
        CREATE FUNCTION public.tunza_content_advance_template() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
        DECLARE required_permission text; operation text; author uuid; parent uuid;
        BEGIN
            IF TG_RELID <> 'public.content_events'::regclass OR TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'Content counter trigger only' USING ERRCODE = '42501';
            END IF;
            SELECT CASE NEW.event_type
                WHEN 'draft_created' THEN 'content.author'
                WHEN 'draft_edited' THEN 'content.author'
                WHEN 'submitted' THEN 'content.submit'
                WHEN 'approved' THEN 'content.review'
                WHEN 'rejected' THEN 'content.review'
                WHEN 'published' THEN 'content.publish'
                WHEN 'suspended' THEN 'content.suspend'
                WHEN 'retired' THEN 'content.suspend' END,
                CASE NEW.event_type
                WHEN 'draft_created' THEN 'content.create'
                WHEN 'draft_edited' THEN 'content.edit'
                WHEN 'submitted' THEN 'content.submit'
                WHEN 'approved' THEN 'content.review'
                WHEN 'rejected' THEN 'content.review'
                WHEN 'published' THEN 'content.publish'
                WHEN 'suspended' THEN 'content.suspend'
                WHEN 'retired' THEN 'content.retire' END
                INTO required_permission, operation;
            SELECT v.author_id, v.template_id INTO author, parent
                FROM public.template_versions v WHERE v.id = NEW.version_id
                AND v.version = NEW.sequence AND (v.state = NEW.event_type OR
                    (v.state = 'draft' AND NEW.event_type IN ('draft_created','draft_edited')));
            IF NOT FOUND OR required_permission IS NULL
                OR NEW.actor_id IS DISTINCT FROM public.tunza_current_uuid('tunza.actor_id')
                OR NOT public.tunza_content_permission(required_permission)
                OR (NEW.event_type IN ('draft_created','draft_edited','submitted')
                    AND NEW.actor_id <> author)
                OR (NEW.event_type IN ('approved','rejected') AND NEW.actor_id = author)
                OR NOT EXISTS (SELECT 1 FROM public.audit_events a
                    WHERE a.id = NEW.audit_event_id AND a.actor_id = NEW.actor_id
                    AND a.permission = required_permission AND a.action = operation
                    AND a.resource_type = 'content' AND a.resource_id = NEW.version_id
                    AND a.outcome = 'success' AND a.metadata->>'state' =
                        CASE WHEN NEW.event_type IN ('draft_created','draft_edited')
                            THEN 'draft' ELSE NEW.event_type END
                    -- xmin is database provenance, not a caller-set session flag.
                    AND a.xmin = pg_current_xact_id()::text::xid)
            THEN RAISE EXCEPTION 'Authorized content operation and current audit required'
                USING ERRCODE = '42501'; END IF;
            UPDATE public.message_templates SET version = version + 1 WHERE id = parent;
            IF NOT FOUND THEN RAISE EXCEPTION 'Content template unavailable'; END IF;
            RETURN NEW;
        END $$
    """)
    # Trigger invocation needs no caller EXECUTE grant. No callable counter API is exposed.
    op.execute("REVOKE ALL ON FUNCTION public.tunza_content_advance_template() FROM PUBLIC")
    op.execute("""
        CREATE TRIGGER content_advance_template AFTER INSERT ON content_events
        FOR EACH ROW EXECUTE FUNCTION public.tunza_content_advance_template()
    """)


def downgrade():
    op.execute("DROP TRIGGER content_advance_template ON content_events")
    op.execute("DROP FUNCTION public.tunza_content_advance_template()")
    op.drop_constraint("uq_content_event_audit", "content_events", type_="unique")
    op.drop_constraint("fk_content_event_audit", "content_events", type_="foreignkey")
    op.drop_column("content_events", "audit_event_id")
    op.execute("GRANT UPDATE (version) ON message_templates TO tunza_runtime")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tunza_fixture_runtime') THEN
            GRANT UPDATE (version) ON message_templates TO tunza_fixture_runtime;
        END IF; END $$""")
