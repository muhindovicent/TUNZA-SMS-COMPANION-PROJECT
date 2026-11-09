"""Staff identity, authentication, facility scope and administration blueprints."""

from flask import Blueprint

auth_blueprint = Blueprint("auth", __name__, url_prefix="/api/v1/auth")
facilities_blueprint = Blueprint("facilities", __name__, url_prefix="/api/v1/facilities")
admin_blueprint = Blueprint("admin", __name__, url_prefix="/api/v1")
