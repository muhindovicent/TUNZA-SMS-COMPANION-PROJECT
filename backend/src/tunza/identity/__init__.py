"""Staff identity, authentication and facility scope blueprints."""

from flask import Blueprint

auth_blueprint = Blueprint("auth", __name__, url_prefix="/api/v1/auth")
facilities_blueprint = Blueprint("facilities", __name__, url_prefix="/api/v1/facilities")
