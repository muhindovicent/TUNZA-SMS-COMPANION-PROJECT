"""Assigned synthetic pregnancy episodes and immutable, versioned dating history."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from flask import g, jsonify, request
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
from tunza.patients.routes import (
    _active_membership,
    _advisory_lock_key,
    _assigned_profiles,
    _parse_uuid,
    _validate_idempotency_key,
)
from tunza.patients.security import establish_evidence_patient_context, establish_patient_context
from tunza.pregnancy import blueprint


def _error(code: str, message: str, status: int):
    return jsonify(error={"code": code, "message": message, "request_id": g.request_id}), status


def _unavailable():
    db.session.rollback()
    return _error("service_unavailable", "The service could not complete the request.", 503)


def _record_pregnancy_audit(
    *, context, permission, action, facility_id, episode_id=None, patient_id=None, metadata=None
):
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


def _deny(context, permission):
    record_audit(
        permission=permission,
        action="pregnancy.access",
        resource_type="pregnancy_episode",
        outcome="denied",
        request_id=g.request_id,
        actor_id=context.user.id,
        metadata={"reason": "unavailable"},
    )
    db.session.commit()
    return _error("not_found", "The request could not be completed.", 404)


def _scope(context, facility_id, permission):
    return (
        facility_id is not None
        and facility_scope_allows(context, facility_id, permission)
        and _active_membership(context, facility_id) is not None
    )


def _episode(episode_id, facility_id, *, lock=False):
    # RLS independently checks the actor's exact episode assignment. Origin
    # facility_id on the episode is provenance, not a substitute for responsibility.
    query = select(PregnancyEpisode).where(
        PregnancyEpisode.id == episode_id,
        sa.func.tunza_pregnancy_assigned(PregnancyEpisode.id),
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return db.session.scalar(query)


def _revisions(episode):
    return (
        select(PregnancyDatingRevision)
        .where(
            PregnancyDatingRevision.episode_id == episode.id,
            PregnancyDatingRevision.patient_id == episode.patient_id,
        )
        .order_by(PregnancyDatingRevision.revision_sequence.desc())
    )


def _revision_data(row):
    return {
        "id": str(row.id),
        "episode_id": str(row.episode_id),
        "patient_id": str(row.patient_id),
        "facility_id": str(row.facility_id),
        "basis": row.basis,
        "estimated_due_date": row.estimated_due_date.isoformat(),
        "reason": row.reason,
        "recorded_by": str(row.recorded_by),
        "revision_sequence": row.revision_sequence,
        "supersedes_id": str(row.supersedes_id) if row.supersedes_id else None,
        "created_at": row.created_at.isoformat(),
        "version": row.version,
        "parent_version": row.parent_version,
    }


def _episode_data(episode, facility_id):
    establish_evidence_patient_context(episode.patient_id)
    latest = db.session.scalar(_revisions(episode).limit(1))
    assignment = db.session.scalar(
        select(EpisodeFacilityAssignment).where(
            EpisodeFacilityAssignment.episode_id == episode.id,
            EpisodeFacilityAssignment.facility_id == facility_id,
            EpisodeFacilityAssignment.valid_from <= datetime.now(UTC),
            sa.or_(
                EpisodeFacilityAssignment.valid_to.is_(None),
                EpisodeFacilityAssignment.valid_to > datetime.now(UTC),
            ),
        )
    )
    return {
        "id": str(episode.id),
        "patient_id": str(episode.patient_id),
        "facility_id": str(facility_id),
        "lifecycle": episode.lifecycle,
        "communication_state": episode.communication_state,
        "version": episode.version,
        "assignment_id": str(assignment.id) if assignment else None,
        "dating_summary": {**_revision_data(latest), "revision_id": str(latest.id)}
        if latest
        else None,
    }


def _response(data, status=200, *, version=None, location=None):
    response = jsonify(data=data)
    if version is not None:
        response.headers["ETag"] = f'"{version}"'
    if location:
        response.headers["Location"] = location
    return response, status


@blueprint.post("")
@require_permission("pregnancy.create")
def create_pregnancy_episode():
    context = get_access_context()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != {"patient_id", "facility_id"}:
        return _error("validation_failed", "Request validation failed.", 422)
    patient_id = _parse_uuid(payload["patient_id"])
    facility_id = _parse_uuid(payload["facility_id"])
    key = _validate_idempotency_key()
    if patient_id is None or facility_id is None or key is None:
        return _error("validation_failed", "Valid IDs and Idempotency-Key are required.", 422)
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "operation": "pregnancy.create",
                "actor": str(context.user.id),
                "patient": str(patient_id),
                "facility": str(facility_id),
            },
            sort_keys=True,
        ).encode()
    ).digest()
    try:
        if not _scope(context, facility_id, "pregnancy.create"):
            return _deny(context, "pregnancy.create")
        establish_patient_context(context, facility_id)
        profile = db.session.scalar(
            _assigned_profiles(context, facility_id).where(
                FacilityPatientProfile.patient_id == patient_id
            )
        )
        if profile is None:
            return _deny(context, "pregnancy.create")
        # All creation for a patient is serialized, even across facilities.
        db.session.execute(
            sa.text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": _advisory_lock_key("pregnancy", patient_id, patient_id)},
        )
        existing = db.session.scalar(
            select(PregnancyEpisode).where(
                PregnancyEpisode.facility_id == facility_id, PregnancyEpisode.idempotency_key == key
            )
        )
        if existing is not None:
            if existing.created_by != context.user.id or existing.request_hash != fingerprint:
                db.session.rollback()
                return _error("conflict", "The idempotency key was already used.", 409)
            episode = existing
            action = "pregnancy.replay"
        else:
            ongoing = db.session.scalar(
                select(PregnancyEpisode.id).where(
                    PregnancyEpisode.patient_id == patient_id,
                    PregnancyEpisode.lifecycle == "ongoing",
                )
            )
            if ongoing is not None:
                db.session.rollback()
                return _error("conflict", "An ongoing pregnancy episode already exists.", 409)
            now = datetime.now(UTC)
            episode = PregnancyEpisode(
                id=uuid4(),
                patient_id=patient_id,
                facility_id=facility_id,
                lifecycle="ongoing",
                communication_state="paused",
                idempotency_key=key,
                request_hash=fingerprint,
                created_by=context.user.id,
            )
            assignment = EpisodeFacilityAssignment(
                id=uuid4(),
                episode_id=episode.id,
                patient_id=patient_id,
                facility_id=facility_id,
                valid_from=now,
            )
            participant = ParticipantAssignment(
                id=uuid4(),
                episode_facility_assignment_id=assignment.id,
                facility_id=facility_id,
                membership_id=_active_membership(context, facility_id).id,
                duty="primary",
                valid_from=now,
            )
            for row in (episode, assignment, participant):
                db.session.add(row)
                db.session.flush()
            action = "pregnancy.create"
        data = _episode_data(episode, facility_id)
        _record_pregnancy_audit(
            context=context,
            permission="pregnancy.create",
            action=action,
            facility_id=facility_id,
            episode_id=episode.id,
        )
        db.session.commit()
        return _response(
            data,
            201,
            version=data["version"],
            location=f"/api/v1/pregnancy-episodes/{data['id']}?facility_id={facility_id}",
        )
    except IntegrityError:
        db.session.rollback()
        return _error("conflict", "The pregnancy episode could not be created.", 409)
    except SQLAlchemyError:
        return _unavailable()


@blueprint.get("")
@require_permission("pregnancy.read")
def list_pregnancy_episodes():
    context = get_access_context()
    facility_id = _parse_uuid(request.args.get("facility_id"))
    patient_id = _parse_uuid(request.args.get("patient_id"))
    if facility_id is None or patient_id is None:
        return _error("validation_failed", "Valid facility and patient IDs are required.", 422)
    try:
        if not _scope(context, facility_id, "pregnancy.read"):
            return _deny(context, "pregnancy.read")
        establish_patient_context(context, facility_id)
        profile = db.session.scalar(
            _assigned_profiles(context, facility_id).where(
                FacilityPatientProfile.patient_id == patient_id
            )
        )
        if profile is None:
            return _deny(context, "pregnancy.read")
        episodes = db.session.scalars(
            select(PregnancyEpisode)
            .where(PregnancyEpisode.patient_id == patient_id)
            .order_by(PregnancyEpisode.created_at.desc(), PregnancyEpisode.id)
        ).all()
        data = [_episode_data(row, facility_id) for row in episodes]
        _record_pregnancy_audit(
            context=context,
            permission="pregnancy.read",
            action="pregnancy.list",
            facility_id=facility_id,
            patient_id=patient_id,
        )
        db.session.commit()
        return _response(data)
    except SQLAlchemyError:
        return _unavailable()


def _read_episode(episode_id, *, history=False):
    context = get_access_context()
    facility_id = _parse_uuid(request.args.get("facility_id"))
    if facility_id is None:
        return _error("validation_failed", "A valid facility context is required.", 422)
    try:
        if not _scope(context, facility_id, "pregnancy.read"):
            return _deny(context, "pregnancy.read")
        establish_patient_context(context, facility_id)
        episode = _episode(episode_id, facility_id)
        if episode is None:
            return _deny(context, "pregnancy.read")
        establish_evidence_patient_context(episode.patient_id)
        data = (
            [_revision_data(row) for row in db.session.scalars(_revisions(episode)).all()]
            if history
            else _episode_data(episode, facility_id)
        )
        version = episode.version
        _record_pregnancy_audit(
            context=context,
            permission="pregnancy.read",
            action="pregnancy.dating.history" if history else "pregnancy.read",
            facility_id=facility_id,
            episode_id=episode_id,
        )
        # Materialize everything before commit clears transaction-local RLS.
        db.session.commit()
        return _response(data, version=version)
    except SQLAlchemyError:
        return _unavailable()


@blueprint.get("/<uuid:episode_id>")
@require_permission("pregnancy.read")
def get_pregnancy_episode(episode_id: UUID):
    return _read_episode(episode_id)


@blueprint.get("/<uuid:episode_id>/dating-revisions")
@require_permission("pregnancy.read")
def list_dating_revisions(episode_id: UUID):
    return _read_episode(episode_id, history=True)


@blueprint.post("/<uuid:episode_id>/dating-revisions")
@require_permission("pregnancy.date")
def create_dating_revision(episode_id: UUID):
    context = get_access_context()
    facility_id = _parse_uuid(request.args.get("facility_id"))
    if facility_id is None:
        return _error("validation_failed", "A valid facility context is required.", 422)
    try:
        if not _scope(context, facility_id, "pregnancy.date"):
            return _deny(context, "pregnancy.date")
        establish_patient_context(context, facility_id)
        episode = _episode(episode_id, facility_id, lock=True)
        if episode is None:
            return _deny(context, "pregnancy.date")
        establish_evidence_patient_context(episode.patient_id)
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {
            "basis",
            "estimated_due_date",
            "reason",
        }:
            return _error("validation_failed", "Request validation failed.", 422)
        basis, edd, reason = (payload[field] for field in ("basis", "estimated_due_date", "reason"))
        key = _validate_idempotency_key()
        if (
            key is None
            or not isinstance(basis, str)
            or not 1 <= len(basis.strip()) <= 64
            or not isinstance(reason, str)
            or not 1 <= len(reason.strip()) <= 1000
            or not isinstance(edd, str)
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", edd)
        ):
            return _error("validation_failed", "Request validation failed.", 422)
        try:
            due_date = date.fromisoformat(edd)
        except ValueError:
            return _error("validation_failed", "A valid YYYY-MM-DD date is required.", 422)
        basis, reason = basis.strip(), reason.strip()
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "operation": "pregnancy.date",
                    "actor": str(context.user.id),
                    "episode": str(episode_id),
                    "patient": str(episode.patient_id),
                    "facility": str(facility_id),
                    "basis": basis,
                    "estimated_due_date": edd,
                    "reason": reason,
                },
                sort_keys=True,
            ).encode()
        ).digest()
        existing = db.session.scalar(
            select(PregnancyDatingRevision).where(
                PregnancyDatingRevision.facility_id == facility_id,
                PregnancyDatingRevision.idempotency_key == key,
            )
        )
        if existing is not None:
            if existing.recorded_by != context.user.id or existing.request_hash != fingerprint:
                db.session.rollback()
                return _error("conflict", "The idempotency key was already used.", 409)
            row = existing
            action = "pregnancy.dating.replay"
        else:
            match = request.headers.get("If-Match")
            if match is None:
                return _error("precondition_required", "An If-Match version is required.", 428)
            if match != f'"{episode.version}"':
                return _error("precondition_failed", "The episode has changed. Reload it.", 412)
            if episode.lifecycle != "ongoing":
                return _error("conflict", "Dating requires an ongoing episode.", 409)
            latest = db.session.scalar(_revisions(episode).limit(1))
            episode.version += 1
            row = PregnancyDatingRevision(
                id=uuid4(),
                episode_id=episode_id,
                patient_id=episode.patient_id,
                facility_id=facility_id,
                basis=basis,
                estimated_due_date=due_date,
                reason=reason,
                recorded_by=context.user.id,
                idempotency_key=key,
                request_hash=fingerprint,
                parent_version=episode.version,
                revision_sequence=latest.revision_sequence + 1 if latest else 1,
                supersedes_id=latest.id if latest else None,
            )
            db.session.add(row)
            db.session.flush()
            action = "pregnancy.date"
        data = _revision_data(row)
        _record_pregnancy_audit(
            context=context,
            permission="pregnancy.date",
            action=action,
            facility_id=facility_id,
            episode_id=episode_id,
            metadata={"method": basis},
        )
        db.session.commit()
        return _response(data, 201, version=data["parent_version"])
    except IntegrityError:
        db.session.rollback()
        return _error("conflict", "The dating revision could not be recorded.", 409)
    except SQLAlchemyError:
        return _unavailable()
