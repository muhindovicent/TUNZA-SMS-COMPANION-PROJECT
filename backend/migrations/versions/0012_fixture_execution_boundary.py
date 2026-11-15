"""Remove fixture execution from the standard application database principal.

Revision ID: 0012_fixture_execution_boundary
Revises: 0011_controlled_transfers
"""

from alembic import op

revision = "0012_fixture_execution_boundary"
down_revision = "0011_controlled_transfers"
branch_labels = None
depends_on = None

SIGNATURE = "(uuid,integer,text,text,uuid,text,bytea,text)"


def _owner_only(name):
    # Renaming preserves all old ACLs, including any non-default grants. Strip
    # every non-owner grant rather than assuming only tunza_runtime had access.
    op.execute(f"REVOKE ALL ON FUNCTION public.{name}{SIGNATURE} FROM PUBLIC")
    op.execute(f"""
        DO $$ DECLARE grantee_name text; BEGIN
            FOR grantee_name IN
                SELECT DISTINCT r.rolname FROM pg_proc p
                CROSS JOIN LATERAL aclexplode(p.proacl) acl
                JOIN pg_roles r ON r.oid = acl.grantee
                WHERE p.oid = 'public.{name}{SIGNATURE}'::regprocedure
                  AND acl.grantee <> p.proowner
            LOOP
                EXECUTE format('REVOKE ALL ON FUNCTION public.{name}{SIGNATURE} FROM %I',
                               grantee_name);
            END LOOP;
        END $$
    """)


def upgrade():
    op.execute(
        f"ALTER FUNCTION public.tunza_synthetic_transfer_decide{SIGNATURE} "
        "RENAME TO tunza_synthetic_transfer_apply_private"
    )
    _owner_only("tunza_synthetic_transfer_apply_private")
    # The private worker has no independent definer authority. It runs with
    # owner privileges only while called inside the authenticated wrapper.
    op.execute(
        f"ALTER FUNCTION public.tunza_synthetic_transfer_apply_private{SIGNATURE} SECURITY INVOKER"
    )
    op.execute("""
        CREATE FUNCTION public.tunza_synthetic_transfer_decide(
            target uuid, expected integer, result text, reason text, receiver uuid,
            fixture_language text, empty_profile bytea, key_version text)
        RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp SET row_security = off AS $$
        BEGIN
            -- session_user and current_database identify the authenticated
            -- principal/database; SET ROLE and caller-settable GUCs cannot forge them.
            IF session_user <> 'tunza_fixture_runtime'
               OR current_database() !~ '^tunza_[a-z0-9_]+_(test|synthetic)$'
            THEN RAISE EXCEPTION 'Fixture execution principal required'
                USING ERRCODE = '42501'; END IF;
            RETURN public.tunza_synthetic_transfer_apply_private(
                target, expected, result, reason, receiver,
                fixture_language, empty_profile, key_version);
        END $$
    """)
    _owner_only("tunza_synthetic_transfer_decide")
    # Provisioning a fixture login is a separate owner-only non-production action.
    # A normal upgrade creates no login and grants no fixture execution capability.
    for table in (
        "facility_transfers",
        "transfer_handover_records",
        "transfer_decisions",
        "transfer_idempotency",
    ):
        op.execute(f"REVOKE ALL ON public.{table} FROM tunza_runtime, PUBLIC")
    for signature in (
        "tunza_transfer_permission(uuid,text,boolean)",
        "tunza_transfer_visible(uuid,uuid,uuid,uuid)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM tunza_runtime, PUBLIC")


def downgrade():
    # Security revocation is intentionally not undone. Returning to 0011's schema
    # must never silently restore the original production-runtime capability.
    op.execute(f"DROP FUNCTION public.tunza_synthetic_transfer_decide{SIGNATURE}")
    op.execute(
        f"ALTER FUNCTION public.tunza_synthetic_transfer_apply_private{SIGNATURE} "
        "RENAME TO tunza_synthetic_transfer_decide"
    )
    op.execute(f"ALTER FUNCTION public.tunza_synthetic_transfer_decide{SIGNATURE} SECURITY DEFINER")
    _owner_only("tunza_synthetic_transfer_decide")
