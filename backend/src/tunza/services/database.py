"""Read-only connectivity checks that expose no connection details."""

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError


def database_is_available(engine: Engine) -> bool:
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT 1")).scalar_one() == 1
    except SQLAlchemyError:
        # No DSN, hostname, username, query parameters, or driver errors in the response/log.
        return False
