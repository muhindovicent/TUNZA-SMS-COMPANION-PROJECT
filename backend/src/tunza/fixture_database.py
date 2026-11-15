"""Owner-only provisioning for an isolated synthetic transfer application login.

Run explicitly after migrations: python -m tunza.fixture_database
No production migration creates or grants this login. No password is emitted or
copied; use separately provisioned credentials, or the disposable CI trust service.
"""

import os
import re

from sqlalchemy import create_engine, text

from tunza.config import postgres_url

FIXTURE_ROLE = "tunza_fixture_runtime"
DATABASE_PATTERN = r"^tunza_[a-z0-9_]+_(test|synthetic)$"
DECIDE = "public.tunza_synthetic_transfer_decide(uuid,integer,text,text,uuid,text,bytea,text)"
PRIVATE = (
    "public.tunza_synthetic_transfer_apply_private(uuid,integer,text,text,uuid,text,bytea,text)"
)


def provision_fixture_role(owner_value, *, environment):
    url = postgres_url(owner_value, "MIGRATION_DATABASE_URL")
    if (
        environment not in {"testing", "development"}
        or url.host not in {"127.0.0.1", "localhost", "::1"}
        or not re.fullmatch(DATABASE_PATTERN, url.database)
        or url.username in {"tunza_runtime", FIXTURE_ROLE}
    ):
        raise RuntimeError(
            "Fixture provisioning requires a separate owner and isolated loopback "
            "synthetic database."
        )
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.begin() as connection:
            database = connection.scalar(text("SELECT current_database()"))
            if database != url.database or not re.fullmatch(DATABASE_PATTERN, database):
                raise RuntimeError("Unexpected fixture database identity.")
            owner_authorized = connection.scalar(
                text("""
                SELECT current_user = session_user AND (
                    r.rolsuper OR (r.rolcreaterole AND r.oid = p.proowner))
                FROM pg_roles r CROSS JOIN pg_proc p
                WHERE r.rolname = session_user AND p.oid = to_regprocedure(:function)
            """),
                {"function": DECIDE},
            )
            if not owner_authorized:
                raise RuntimeError(
                    "Fixture provisioning requires an authenticated database owner/provisioner."
                )
            if connection.scalar(
                text("SELECT has_function_privilege('tunza_runtime', :function, 'EXECUTE')"),
                {"function": DECIDE},
            ):
                raise RuntimeError(
                    "Apply the fixture execution security migration before provisioning."
                )
            connection.execute(
                text(f"""
                DO $$ BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{FIXTURE_ROLE}') THEN
                        CREATE ROLE {FIXTURE_ROLE} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB
                            NOCREATEROLE NOREPLICATION NOBYPASSRLS;
                    END IF;
                END $$
            """)
            )
            safe = connection.scalar(
                text("""
                SELECT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
                    AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication
                    AND NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member = r.oid)
                    AND NOT EXISTS (SELECT 1 FROM pg_database WHERE datdba = r.oid)
                    AND NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspowner = r.oid)
                    AND NOT EXISTS (SELECT 1 FROM pg_class WHERE relowner = r.oid)
                    AND NOT EXISTS (SELECT 1 FROM pg_proc WHERE proowner = r.oid)
                    AND NOT has_schema_privilege(r.rolname, 'public', 'CREATE')
                FROM pg_roles r WHERE rolname = :role
            """),
                {"role": FIXTURE_ROLE},
            )
            if not safe:
                raise RuntimeError("Existing fixture role is not an isolated non-owner principal.")
            quote = connection.dialect.identifier_preparer.quote_identifier
            connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {FIXTURE_ROLE}"))
            # Copy only base table/column DML ACLs, not role membership or owners'
            # powers. Partial UPDATE grants stay partial; all RLS/triggers remain.
            grants = connection.execute(
                text("""
                SELECT c.relname, NULL::text AS column_name, acl.privilege_type
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                CROSS JOIN LATERAL aclexplode(c.relacl) acl
                JOIN pg_roles r ON r.oid = acl.grantee
                WHERE n.nspname = 'public' AND c.relkind IN ('r','p')
                  AND r.rolname = 'tunza_runtime'
                UNION
                SELECT c.relname, a.attname, acl.privilege_type
                FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                CROSS JOIN LATERAL aclexplode(a.attacl) acl
                JOIN pg_roles r ON r.oid = acl.grantee
                WHERE n.nspname = 'public' AND r.rolname = 'tunza_runtime'
            """)
            ).all()
            for grant in grants:
                if grant.privilege_type not in {"SELECT", "INSERT", "UPDATE"}:
                    raise RuntimeError("Unexpected standard-runtime DML privilege.")
                columns = f" ({quote(grant.column_name)})" if grant.column_name else ""
                connection.execute(
                    text(
                        f"GRANT {grant.privilege_type}{columns} "
                        f"ON TABLE public.{quote(grant.relname)} TO {FIXTURE_ROLE}"
                    )
                )
            for signature in (
                "tunza_current_uuid(text)",
                "tunza_pregnancy_assigned(uuid)",
                "tunza_pregnancy_can_complete(uuid)",
                "tunza_content_permission(text)",
                "tunza_appointment_scope(uuid,uuid,uuid,uuid)",
                "tunza_transfer_permission(uuid,text,boolean)",
                "tunza_transfer_visible(uuid,uuid,uuid,uuid)",
            ):
                connection.execute(
                    text(f"GRANT EXECUTE ON FUNCTION public.{signature} TO {FIXTURE_ROLE}")
                )
            connection.execute(
                text(
                    "GRANT SELECT, INSERT ON public.facility_transfers, "
                    "public.transfer_handover_records, "
                    f"public.transfer_idempotency TO {FIXTURE_ROLE}"
                )
            )
            connection.execute(text(f"GRANT SELECT ON public.transfer_decisions TO {FIXTURE_ROLE}"))
            connection.execute(text(f"GRANT EXECUTE ON FUNCTION {DECIDE} TO {FIXTURE_ROLE}"))
            if connection.scalar(
                text("SELECT has_function_privilege(:role, :function, 'EXECUTE')"),
                {"role": FIXTURE_ROLE, "function": PRIVATE},
            ):
                raise RuntimeError("Fixture login must not execute the private transfer worker.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    provision_fixture_role(
        os.environ.get("MIGRATION_DATABASE_URL"), environment=os.environ.get("APP_ENV")
    )
    print("Restricted fixture login provisioned only for this isolated synthetic database.")
