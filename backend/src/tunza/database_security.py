"""Fail closed unless each runtime connection authenticates as a restricted role."""

from flask import Flask
from sqlalchemy import event

from tunza.extensions import db


def register_runtime_role(app: Flask) -> None:
    role = app.config["DATABASE_RUNTIME_ROLE"]
    with app.app_context():
        engine = db.engine

    @event.listens_for(engine, "connect")
    def verify_runtime_role(dbapi_connection, _connection_record) -> None:
        previous_autocommit = dbapi_connection.autocommit
        dbapi_connection.autocommit = True
        try:
            with dbapi_connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT session_user = %s AND current_user = session_user
                        AND r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolbypassrls
                        AND NOT r.rolcreatedb AND NOT r.rolcreaterole AND NOT r.rolreplication
                        AND NOT EXISTS (
                            SELECT 1 FROM pg_roles parent
                            WHERE parent.oid <> r.oid
                              AND pg_has_role(r.oid, parent.oid, 'MEMBER')
                        )
                        AND NOT EXISTS (
                            SELECT 1 FROM pg_database d
                            WHERE d.datname = current_database() AND d.datdba = r.oid
                        )
                        AND NOT EXISTS (
                            SELECT 1 FROM pg_namespace n
                            WHERE n.nspname = 'public' AND n.nspowner = r.oid
                        )
                        AND NOT EXISTS (
                            SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                            WHERE n.nspname = 'public' AND c.relowner = r.oid
                        )
                        AND NOT has_schema_privilege(current_user, 'public', 'CREATE')
                    FROM pg_roles r WHERE r.rolname = session_user
                    """,
                    (role,),
                )
                row = cursor.fetchone()
                if not row or not row[0]:
                    raise RuntimeError(
                        "Runtime database login does not satisfy security requirements."
                    )
        finally:
            dbapi_connection.autocommit = previous_autocommit
