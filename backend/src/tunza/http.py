"""Shared JSON error handling and non-sensitive request correlation."""

from uuid import uuid4

from flask import Flask, g, jsonify
from werkzeug.exceptions import HTTPException


def register_http_handlers(app: Flask) -> None:
    @app.before_request
    def assign_request_id() -> None:
        # Never trust client-provided correlation values as an audit identity.
        g.request_id = str(uuid4())

    @app.after_request
    def response_headers(response):
        response.headers["X-Request-ID"] = g.request_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.errorhandler(HTTPException)
    def http_error(error: HTTPException):
        # Preserve protocol headers such as Allow without echoing descriptions/inputs.
        response = error.get_response()
        response.set_data(
            app.json.dumps(
                {
                    "error": {
                        "code": error.name.lower().replace(" ", "_"),
                        "message": "The request could not be completed.",
                        "request_id": g.request_id,
                    }
                }
            )
        )
        response.content_type = "application/json"
        return response

    @app.errorhandler(Exception)
    def unexpected_error(_error: Exception):
        # Driver exceptions may embed DSNs/parameters. Do not log exception text/traceback.
        app.logger.error("unhandled_request_error request_id=%s", g.request_id)
        return jsonify(
            error={
                "code": "internal_error",
                "message": "The service could not complete the request.",
                "request_id": g.request_id,
            }
        ), 500
