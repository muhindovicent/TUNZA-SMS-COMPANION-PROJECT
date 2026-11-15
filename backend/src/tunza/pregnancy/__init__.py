"""Pregnancy episode and dating history blueprint."""

from flask import Blueprint

blueprint = Blueprint("pregnancy", __name__, url_prefix="/api/v1/pregnancy-episodes")

__all__ = ["blueprint"]
