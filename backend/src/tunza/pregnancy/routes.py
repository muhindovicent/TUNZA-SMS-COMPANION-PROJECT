"""Pregnancy episode creation, retrieval and dating revision workflows."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from flask import Response, g, jsonify, request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from tunza.access import facility_scope_allows, get_access_context, require_permission
from tunza.audit import record_audit
from tunza.extensions import db
from tunza.patients.models import (
    EpisodeFacilityAssignment,
    FacilityPatientProfile,
    ParticipantAssignment,
    PregnancyDatingRevision,
    PregnancyEpisode,
)
from tunza.patients.security import establish_evidence_patient_context, establish_patient_context
from tunza.pregnancy import blueprint

MAX_IDEMPOTENCY_KEY_LENGTH = 128
UNKNOWN_FIELD_MESSAGE = "Request validation failed."
ALLOWED_DATING_BASIS = frozenset({"lmp", "ultrasound", "clinical", "fundal_height"})


def _error(code: str, message: str, status: int) -> tuple[Response, int]:
    return jsonify(error={"code": code, "message": message, "request_id": g.request_id}), status


def _service_unavailable() -> tuple[Response, int]:
    return _error("service_unavailable", "The service could not complete the request.", 503)


def _parse_uuid(value: object) -> UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _validate_idempotency_key() -> str | None:
    value = request.headers.get("Idempotency-Key")
    if not value or len(value) > MAX_IDEMPOTENCY_KEY_LENGTH:
        return None
    if any(ord(character) < 33 or ord(character) > 126 for character in value):
        return None
    return value


def _advisory_lock_key(namespace: str, patient_id: UUID, facility_id: UUID) -> int:
    """Deterministic transaction-scoped advisory lock key (stable across processes)."""
    digest = hashlib.sha256(f"{namespace}:{patient_id}:{facility_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % (2**31)


def _active_membership(context, facility_id: UUID):
    return next(
        (membership for membership in context.memberships if membership.facility_id == facility_id),
        None,
    )


def _record_pregnancy_audit(
    *,
    context,
    permission: str,
    action: str,
    facility_id: UUID,
    patient_id: UUID | None,
    episode_id: UUID | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    record_audit(
        permission=permission,
        action=action,
        resource_type="pregnancy_episode",
        outcome="success",
        request_id=g.request_id,
        actor_id=context.user.id,
        facility_id=facility_id,
        resource_id=episode_id or patient_id,
        metadata=metadata,
    )


def _deny_pregnancy_access(context, permission: str, reason: str) -> tuple[Response, int]:
    try:
        record_audit(
            permission=permission,
            action="pregnancy.access",
            resource_type="pregnancy_episode",
            outcome="denied",
            request_id=g.request_id,
            actor_id=context.user.id,
            facility_id=None,
            metadata={"reason": reason},
        )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return _service_unavailable()
    return _error("not_found", "The request could not be completed.", 404)


def _pregnancy_projection(
    episode: PregnancyEpisode,
    assignment: EpisodeFacilityAssignment | None,
    dating_revisions: list[PregnancyDatingRevision] | None = None,
) -> dict[str, object]:
    latest_dating = None
    if dating_revisions:
        latest_dating = dating_revisions[0]
    return {
        "id": str(episode.id),
        "patient_id": str(episode.patient_id),
        "facility_id": str(episode.facility_id),
        "lifecycle": episode.lifecycle,
        "communication_state": episode.communication_state,
        "version": episode.version,
        "assignment_id": str(assignment.id) if assignment else None,
        "dating_summary": (
            {
                "revision_id": str(latest_dating.id),
                "basis": latest_dating.basis,
                "estimated_due_date": latest_dating.estimated_due_date.isoformat(),
                "reason": latest_dating.reason,
                "revision_sequence": latest_dating.revision_sequence,
                "supersedes_id": (
                    str(latest_dating.supersedes_id) if latest_dating.supersedes_id else None
                ),
            }
            if latest_dating
            else None
        ),
    }


@blueprint.post("")
@require_permission("pregnancy.create")
def create_pregnancy_episode() -> tuple[Response, int]:
    """Create a new pregnancy episode for an assigned synthetic patient."""
    context = get_access_context()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error("validation_failed", "A JSON request body is required.", 422)
    if set(payload) - {"patient_id", "facility_id"}:
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
    patient_id = _parse_uuid(payload.get("patient_id"))
    facility_id = _parse_uuid(payload.get("facility_id"))
    if patient_id is None or facility_id is None:
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
    idempotency_key = _validate_idempotency_key()
    if idempotency_key is None:
        return _error("validation_failed", "A valid Idempotency-Key is required.", 422)
    if not facility_scope_allows(context, facility_id, "pregnancy.create"):
        return _deny_pregnancy_access(context, "pregnancy.create", "facility_scope")
    membership = _active_membership(context, facility_id)
    if membership is None:
        return _deny_pregnancy_access(context, "pregnancy.create", "facility_scope")
    # Verify the patient is assigned to this facility
    establish_patient_context(context, facility_id)
    profile = db.session.scalar(
        select(FacilityPatientProfile).where(
            FacilityPatientProfile.patient_id == patient_id,
            FacilityPatientProfile.facility_id == facility_id,
        )
    )
    if profile is None:
        return _deny_pregnancy_access(context, "pregnancy.create", "unavailable")
    request_hash = hashlib.sha256(
        json.dumps(
            {
                "patient_id": str(patient_id),
                "facility_id": str(facility_id),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).digest()
    # Idempotency: check if this key was already used for episode creation
    existing = db.session.scalar(
        select(PregnancyEpisode).where(
            PregnancyEpisode.facility_id == facility_id,
            PregnancyEpisode.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.patient_id != patient_id or existing.request_hash != request_hash:
            return _error(
                "conflict", "The idempotency key was already used with a different request.", 409
            )
        response_data = _pregnancy_projection(existing, None)
        return jsonify(data=response_data), 201
    try:
        lock_id = _advisory_lock_key("pregnancy", patient_id, facility_id)
        db.session.execute(
            sa.text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": lock_id},
        )
        # Re-check idempotency under lock
        existing = db.session.scalar(
            select(PregnancyEpisode).where(
                PregnancyEpisode.facility_id == facility_id,
                PregnancyEpisode.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.patient_id != patient_id or existing.request_hash != request_hash:
                db.session.rollback()
                return _error("conflict", "The idempotency key was already used.", 409)
            response_data = _pregnancy_projection(existing, None)
            db.session.commit()
            return jsonify(data=response_data), 201
        now = datetime.now(UTC)
        episode = PregnancyEpisode(
            id=uuid4(),
            patient_id=patient_id,
            facility_id=facility_id,
            lifecycle="ongoing",
            communication_state="paused",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        db.session.add(episode)
        db.session.flush()
        assignment = EpisodeFacilityAssignment(
            id=uuid4(),
            episode_id=episode.id,
            patient_id=patient_id,
            facility_id=facility_id,
            valid_from=now,
        )
        db.session.add(assignment)
        db.session.flush()
        participant_assignment = ParticipantAssignment(
            id=uuid4(),
            episode_facility_assignment_id=assignment.id,
            facility_id=facility_id,
            membership_id=membership.id,
            duty="primary",
            valid_from=now,
        )
        db.session.add(participant_assignment)
        db.session.flush()
        _record_pregnancy_audit(
            context=context,
            permission="pregnancy.create",
            action="pregnancy.create",
            facility_id=facility_id,
            patient_id=patient_id,
            episode_id=episode.id,
        )
        response_data = _pregnancy_projection(episode, assignment)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _error("conflict", "The pregnancy episode could not be created.", 409)
    except SQLAlchemyError:
        db.session.rollback()
        return _service_unavailable()
    return jsonify(data=response_data), 201


@blueprint.get("/<uuid:episode_id>")
@require_permission("pregnancy.read")
def get_pregnancy_episode(episode_id: UUID) -> tuple[Response, int]:
    """Retrieve a pregnancy episode with its latest dating revision."""
    context = get_access_context()
    facility_id = _parse_uuid(request.args.get("facility_id"))
    if facility_id is None:
        return _error("validation_failed", "A valid facility context is required.", 422)
    if not facility_scope_allows(context, facility_id, "pregnancy.read"):
        return _deny_pregnancy_access(context, "pregnancy.read", "facility_scope")
    membership = _active_membership(context, facility_id)
    if membership is None:
        return _deny_pregnancy_access(context, "pregnancy.read", "facility_scope")
    establish_patient_context(context, facility_id)
    episode = db.session.scalar(
        select(PregnancyEpisode).where(
            PregnancyEpisode.id == episode_id,
            PregnancyEpisode.facility_id == facility_id,
        )
    )
    if episode is None:
        return _deny_pregnancy_access(context, "pregnancy.read", "unavailable")
    # Verify patient assignment
    profile = db.session.scalar(
        select(FacilityPatientProfile).where(
            FacilityPatientProfile.patient_id == episode.patient_id,
            FacilityPatientProfile.facility_id == facility_id,
        )
    )
    if profile is None:
        return _deny_pregnancy_access(context, "pregnancy.read", "unavailable")
    establish_evidence_patient_context(episode.patient_id)
    assignment = db.session.scalar(
        select(EpisodeFacilityAssignment).where(
            EpisodeFacilityAssignment.episode_id == episode_id,
            EpisodeFacilityAssignment.facility_id == facility_id,
            EpisodeFacilityAssignment.valid_to.is_(None),
        )
    )
    dating_revisions = list(
        db.session.scalars(
            select(PregnancyDatingRevision)
            .where(
                PregnancyDatingRevision.episode_id == episode_id,
                PregnancyDatingRevision.patient_id == episode.patient_id,
                PregnancyDatingRevision.facility_id == facility_id,
            )
            .order_by(PregnancyDatingRevision.revision_sequence.desc())
        ).all()
    )
    try:
        _record_pregnancy_audit(
            context=context,
            permission="pregnancy.read",
            action="pregnancy.read",
            facility_id=facility_id,
            patient_id=episode.patient_id,
            episode_id=episode_id,
        )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return _service_unavailable()
    response_data = _pregnancy_projection(episode, assignment, dating_revisions)
    return jsonify(data=response_data), 200


@blueprint.post("/<uuid:episode_id>/dating-revisions")
@require_permission("pregnancy.date")
def create_dating_revision(episode_id: UUID) -> tuple[Response, int]:
    """Create an append-only pregnancy dating revision."""
    context = get_access_context()
    facility_id = _parse_uuid(request.args.get("facility_id"))
    if facility_id is None:
        return _error("validation_failed", "A valid facility context is required.", 422)
    if not facility_scope_allows(context, facility_id, "pregnancy.date"):
        return _deny_pregnancy_access(context, "pregnancy.date", "facility_scope")
    membership = _active_membership(context, facility_id)
    if membership is None:
        return _deny_pregnancy_access(context, "pregnancy.date", "facility_scope")
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error("validation_failed", "A JSON request body is required.", 422)
    if set(payload) - {"basis", "estimated_due_date", "reason"}:
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
    basis = payload.get("basis")
    estimated_due_date_str = payload.get("estimated_due_date")
    reason = payload.get("reason")
    if (
        not isinstance(basis, str)
        or basis not in ALLOWED_DATING_BASIS
        or not isinstance(estimated_due_date_str, str)
        or not isinstance(reason, str)
        or not reason.strip()
        or len(reason) > 1000
    ):
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
    reason = reason.strip()
    try:
        estimated_due_date = datetime.fromisoformat(estimated_due_date_str.replace("Z", "+00:00"))
        if estimated_due_date.tzinfo is None:
            return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
    except ValueError:
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
    idempotency_key = _validate_idempotency_key()
    if idempotency_key is None:
        return _error("validation_failed", "A valid Idempotency-Key is required.", 422)
    # Verify the episode exists and is assigned to this facility
    establish_patient_context(context, facility_id)
    episode = db.session.scalar(
        select(PregnancyEpisode).where(
            PregnancyEpisode.id == episode_id,
            PregnancyEpisode.facility_id == facility_id,
        )
    )
    if episode is None:
        return _deny_pregnancy_access(context, "pregnancy.date", "unavailable")
    patient_id = episode.patient_id
    request_hash = hashlib.sha256(
        json.dumps(
            {
                "episode_id": str(episode_id),
                "facility_id": str(facility_id),
                "basis": basis,
                "estimated_due_date": estimated_due_date_str,
                "reason": reason,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).digest()
    establish_evidence_patient_context(patient_id)
    # Idempotency: check if this key was already used for a dating revision
    existing = db.session.scalar(
        select(PregnancyDatingRevision).where(
            PregnancyDatingRevision.facility_id == facility_id,
            PregnancyDatingRevision.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if (
            existing.episode_id != episode_id
            or existing.recorded_by != context.user.id
            or existing.request_hash != request_hash
        ):
            return _error(
                "conflict", "The idempotency key was already used with a different request.", 409
            )
        response_data = {
            "id": str(existing.id),
            "episode_id": str(existing.episode_id),
            "basis": existing.basis,
            "estimated_due_date": existing.estimated_due_date.isoformat(),
            "reason": existing.reason,
            "revision_sequence": existing.revision_sequence,
            "supersedes_id": (str(existing.supersedes_id) if existing.supersedes_id else None),
            "version": existing.version,
        }
        return jsonify(data=response_data), 201
    try:
        lock_id = _advisory_lock_key("dating", patient_id, facility_id)
        db.session.execute(
            sa.text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": lock_id},
        )
        # Re-check idempotency under lock
        existing = db.session.scalar(
            select(PregnancyDatingRevision).where(
                PregnancyDatingRevision.facility_id == facility_id,
                PregnancyDatingRevision.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if (
                existing.episode_id != episode_id
                or existing.recorded_by != context.user.id
                or existing.request_hash != request_hash
            ):
                db.session.rollback()
                return _error("conflict", "The idempotency key was already used.", 409)
            response_data = {
                "id": str(existing.id),
                "episode_id": str(existing.episode_id),
                "basis": existing.basis,
                "estimated_due_date": existing.estimated_due_date.isoformat(),
                "reason": existing.reason,
                "revision_sequence": existing.revision_sequence,
                "supersedes_id": (str(existing.supersedes_id) if existing.supersedes_id else None),
                "version": existing.version,
            }
            db.session.commit()
            return jsonify(data=response_data), 201
        # Get the latest revision sequence for this episode
        max_sequence = db.session.scalar(
            select(sa.func.max(PregnancyDatingRevision.revision_sequence)).where(
                PregnancyDatingRevision.episode_id == episode_id,
                PregnancyDatingRevision.patient_id == patient_id,
                PregnancyDatingRevision.facility_id == facility_id,
            )
        )
        next_sequence = (max_sequence or 0) + 1
        # Find the current latest revision to supersede
        latest_revision = db.session.scalar(
            select(PregnancyDatingRevision)
            .where(
                PregnancyDatingRevision.episode_id == episode_id,
                PregnancyDatingRevision.patient_id == patient_id,
                PregnancyDatingRevision.facility_id == facility_id,
            )
            .order_by(PregnancyDatingRevision.revision_sequence.desc())
        )
        revision = PregnancyDatingRevision(
            id=uuid4(),
            episode_id=episode_id,
            patient_id=patient_id,
            facility_id=facility_id,
            basis=basis,
            estimated_due_date=estimated_due_date,
            reason=reason,
            recorded_by=context.user.id,
            revision_sequence=next_sequence,
            supersedes_id=latest_revision.id if latest_revision else None,
        )
        db.session.add(revision)
        db.session.flush()
        # Bump the episode version
        episode.version += 1
        _record_pregnancy_audit(
            context=context,
            permission="pregnancy.date",
            action="pregnancy.date",
            facility_id=facility_id,
            patient_id=patient_id,
            episode_id=episode_id,
            metadata={
                "basis": basis,
                "revision_sequence": next_sequence,
                "supersedes_id": str(revision.supersedes_id) if revision.supersedes_id else None,
            },
        )
        response_data = {
            "id": str(revision.id),
            "episode_id": str(revision.episode_id),
            "basis": revision.basis,
            "estimated_due_date": revision.estimated_due_date.isoformat(),
            "reason": revision.reason,
            "revision_sequence": revision.revision_sequence,
            "supersedes_id": str(revision.supersedes_id) if revision.supersedes_id else None,
            "version": revision.version,
        }
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _error("conflict", "The dating revision could not be created.", 409)
    except SQLAlchemyError:
        db.session.rollback()
        return _service_unavailable()
    return jsonify(data=response_data), 201
