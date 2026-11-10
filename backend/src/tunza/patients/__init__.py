"""Protected patient registry blueprint."""

from flask import Blueprint

blueprint = Blueprint("patients", __name__, url_prefix="/api/v1/patients")

__all__ = ["blueprint"]
