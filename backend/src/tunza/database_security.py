"""Apply the restricted application role to every runtime connection."""

from flask import Flask
from sqlalchemy import event

from tunza.extensions import db


def register_runtime_role(app: Flask) -> None:
    role = app.config["DATABASE_RUNTIME_ROLE"]
    statement = f'SET ROLE "{role}"'
    with app.app_context():
        engine = db.engine

    @event.listens_for(engine, "connect")
    def set_runtime_role(dbapi_connection, _connection_record) -> None:
        # SET ROLE must survive pool rollback; issuing it in an implicit
        # transaction would otherwise restore the privileged login role.
        previous_autocommit = dbapi_connection.autocommit
        dbapi_connection.autocommit = True
        try:
            with dbapi_connection.cursor() as cursor:
                cursor.execute(statement)
        finally:
            dbapi_connection.autocommit = previous_autocommit
