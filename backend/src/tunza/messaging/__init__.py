"""Durable synthetic scheduling; no submission capability."""

from flask import Blueprint

blueprint = Blueprint("messaging", __name__, url_prefix="/api/v1")
