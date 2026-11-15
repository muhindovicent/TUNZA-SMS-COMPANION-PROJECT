"""Opt-in synthetic facility transfers; live transfers remain disabled."""

from flask import Blueprint

blueprint = Blueprint("transfers", __name__, url_prefix="/api/v1/transfers")
