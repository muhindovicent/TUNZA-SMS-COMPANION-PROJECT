"""Sanitized append-only audit event recording within the calling transaction."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from tunza.extensions import db
from tunza.identity.models import AuditEvent

ALLOWED_METADATA_KEYS = frozenset(
    {"reason", "state", "role_code", "scope_kind", "facility_code", "endpoint", "method"}
)
MAX_METADATA_KEYS = 10
MAX_METADATA_VALUE_LENGTH = 200


def sanitize_metadata(values: Mapping[str, Any] | None) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    if not values:
        return clean
    for key, value in list(values.items())[:MAX_METADATA_KEYS]:
        if key not in ALLOWED_METADATA_KEYS:
            continue
        if value is None or isinstance(value, bool | int | float):
            clean[key] = value
        elif isinstance(value, str):
            clean[key] = value[:MAX_METADATA_VALUE_LENGTH]
    return clean


def record_audit(
    *,
    permission: str,
    action: str,
    resource_type: str,
    outcome: str,
    request_id: UUID | str,
    actor_id: UUID | None = None,
    service_actor: str | None = None,
    facility_id: UUID | None = None,
    resource_id: UUID | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> AuditEvent:
    if actor_id is None and service_actor is None:
        raise ValueError("An audit event requires an actor or service identity.")
    event = AuditEvent(
        actor_id=actor_id,
        service_actor=service_actor,
        facility_id=facility_id,
        request_id=UUID(str(request_id)),
        permission=permission,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        event_metadata=sanitize_metadata(metadata),
    )
    db.session.add(event)
    return event
