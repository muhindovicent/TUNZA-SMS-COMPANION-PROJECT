"""Public, non-sensitive infrastructure health blueprint."""

from flask import Blueprint

blueprint = Blueprint("health", __name__)

from tunza.health import routes as routes  # noqa: E402
