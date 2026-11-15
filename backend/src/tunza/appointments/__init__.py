"""Assigned synthetic ANC appointment management."""

from flask import Blueprint

blueprint = Blueprint("appointments", __name__, url_prefix="/api/v1/appointments")
