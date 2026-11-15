"""Synthetic-only versioned content management; no dispatch or clinical sign-off."""

from flask import Blueprint

blueprint = Blueprint("content", __name__, url_prefix="/api/v1")
