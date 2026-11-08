"""Opaque server-side sessions: creation, request loading, revocation and expiry."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from flask import Response, current_app, g, request
from sqlalchemy import select
from sqlalchemy.orm import Session

from tunza.extensions import db
from tunza.identity.models import AuthSession, User

SESSION_G = "tunza_session_record"


def utcnow() -> datetime:
    return datetime.now(UTC)


def hash_session_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def create_session(user: User, session: Session | None = None) -> tuple[AuthSession, str]:
    session = session or db.session
    now = utcnow()
    token = secrets.token_urlsafe(32)
    record = AuthSession(
        user_id=user.id,
        token_hash=hash_session_token(token),
        state="active",
        expires_at=now + timedelta(seconds=current_app.config["SESSION_ABSOLUTE_TIMEOUT_SECONDS"]),
        last_seen_at=now,
        security_version=user.security_version,
    )
    session.add(record)
    session.flush()
    return record, token


def get_current_session() -> tuple[AuthSession | None, User | None]:
    if hasattr(g, SESSION_G):
        return getattr(g, SESSION_G)

    token = request.cookies.get(current_app.config["SESSION_COOKIE_NAME"])
    record: AuthSession | None = None
    user: User | None = None
    if token:
        record = db.session.scalar(
            select(AuthSession).where(AuthSession.token_hash == hash_session_token(token))
        )
        if record is not None:
            now = utcnow()
            idle_cutoff = now - timedelta(
                seconds=current_app.config["SESSION_IDLE_TIMEOUT_SECONDS"]
            )
            valid = (
                record.state == "active"
                and record.expires_at > now
                and record.last_seen_at > idle_cutoff
            )
            if valid:
                candidate = db.session.get(User, record.user_id)
                if (
                    candidate is not None
                    and candidate.status == "active"
                    and candidate.security_version == record.security_version
                ):
                    record.last_seen_at = now
                    db.session.commit()
                    user = candidate
            if user is None:
                record = None

    setattr(g, SESSION_G, (record, user))
    return record, user


def revoke_session(record: AuthSession) -> None:
    record.state = "revoked"
    record.revoked_at = utcnow()


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        current_app.config["SESSION_COOKIE_NAME"],
        token,
        max_age=current_app.config["SESSION_ABSOLUTE_TIMEOUT_SECONDS"],
        httponly=True,
        secure=current_app.config["SESSION_COOKIE_SECURE"],
        samesite="Lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        current_app.config["SESSION_COOKIE_NAME"],
        httponly=True,
        secure=current_app.config["SESSION_COOKIE_SECURE"],
        samesite="Lax",
        path="/",
    )
