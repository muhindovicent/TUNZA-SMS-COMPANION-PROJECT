"""Synthetic completion revisions and current-responsibility ANC closure."""

import hashlib
import json
import re
from datetime import date
from uuid import uuid4

import sqlalchemy as sa
from flask import current_app, jsonify, request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from tunza.access import get_access_context, require_permission
from tunza.appointments.models import Appointment
from tunza.appointments.routes import _audit as _appointment_audit
from tunza.appointments.routes import _event, _iso
from tunza.extensions import db
from tunza.patients.models import EpisodeFacilityAssignment
from tunza.patients.routes import _advisory_lock_key, _parse_uuid, _validate_idempotency_key
from tunza.patients.security import establish_evidence_patient_context, establish_patient_context
from tunza.pregnancy import blueprint
from tunza.pregnancy.completion_models import PregnancyCompletionRecord
from tunza.pregnancy.routes import (
    _deny,
    _episode,
    _error,
    _record_pregnancy_audit,
    _scope,
    _unavailable,
)
from tunza.services.encryption import decrypt_json, encrypt_json


def _crypt(data, purpose):
    return encrypt_json(
        data,
        key=current_app.config["PATIENT_ENCRYPTION_KEY"],
        key_version=current_app.config["PATIENT_KEY_VERSION"],
        purpose=purpose,
    )


def _decrypt(cipher, version, purpose):
    return decrypt_json(
        cipher,
        key=current_app.config["PATIENT_ENCRYPTION_KEY"],
        key_version=version,
        purpose=purpose,
    )


def _response(data):
    response = jsonify(data=data)
    response.headers["ETag"] = f'"{data["parent_version"]}"'
    return response, 201


@blueprint.post("/<uuid:episode_id>/completion")
@require_permission("pregnancy.complete")
def record_completion(episode_id):
    context = get_access_context()
    facility = _parse_uuid(request.args.get("facility_id"))
    if facility is None:
        return _error("validation_failed", "A valid facility context is required.", 422)
    try:
        if not _scope(context, facility, "pregnancy.complete"):
            return _deny(context, "pregnancy.complete")
        establish_patient_context(context, facility)
        episode = _episode(episode_id, facility)
        if episode is None:
            return _deny(context, "pregnancy.complete")
        establish_evidence_patient_context(episode.patient_id)
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) - {
            "completed_on",
            "approved_outcome_fields",
            "reason",
            "supersedes_id",
        }:
            return _error("validation_failed", "Request validation failed.", 422)
        completed_on, reason = payload.get("completed_on"), payload.get("reason")
        supersedes = _parse_uuid(payload.get("supersedes_id"))
        if (
            not isinstance(completed_on, str)
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", completed_on)
            or not isinstance(reason, str)
            or not 1 <= len(reason.strip()) <= 1000
            or not isinstance(payload.get("approved_outcome_fields"), dict)
            or payload["approved_outcome_fields"] != {}
            or (payload.get("supersedes_id") is not None and supersedes is None)
        ):
            return _error(
                "validation_failed",
                "Only a date, reason and empty approved_outcome_fields are supported.",
                422,
            )
        try:
            completed_date = date.fromisoformat(completed_on)
        except ValueError:
            return _error(
                "validation_failed", "A valid YYYY-MM-DD completion date is required.", 422
            )
        key = _validate_idempotency_key()
        if key is None:
            return _error("validation_failed", "A valid Idempotency-Key is required.", 422)
        reason = reason.strip()
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "operation": "pregnancy.completion",
                    "actor": str(context.user.id),
                    "facility": str(facility),
                    "episode": str(episode_id),
                    "patient": str(episode.patient_id),
                    "completed_on": completed_on,
                    "reason": reason,
                    "approved_outcome_fields": {},
                    "supersedes_id": str(supersedes) if supersedes else None,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).digest()
        # Same order as appointment commands: command key, pregnancy, then child rows.
        db.session.execute(
            sa.text("SELECT pg_advisory_xact_lock(:key)"),
            {
                "key": _advisory_lock_key(
                    f"pregnancy.completion:{key}", context.user.id, context.user.id
                )
            },
        )
        replay = db.session.scalar(
            select(PregnancyCompletionRecord).where(
                PregnancyCompletionRecord.recorded_by == context.user.id,
                PregnancyCompletionRecord.idempotency_key == key,
            )
        )
        if replay:
            if replay.request_hash != fingerprint:
                db.session.rollback()
                return _error("conflict", "The idempotency key was already used.", 409)
            data = _decrypt(
                replay.response_cipher, replay.key_version, "pregnancy-completion-response"
            )
            _record_pregnancy_audit(
                context=context,
                permission="pregnancy.complete",
                action="pregnancy.completion.replay",
                facility_id=facility,
                episode_id=episode_id,
            )
            db.session.commit()
            return _response(data)
        episode = _episode(episode_id, facility, lock=True)
        # A transfer may have completed while this statement waited for its lock.
        # Reauthorize in a new statement, with a fresh statement timestamp.
        if episode is None or not db.session.scalar(
            select(sa.func.tunza_pregnancy_can_complete(episode_id))
        ):
            return _deny(context, "pregnancy.complete")
        if request.headers.get("If-Match") is None:
            return _error("precondition_required", "A pregnancy If-Match version is required.", 428)
        if request.headers["If-Match"] != f'"{episode.version}"':
            return _error("precondition_failed", "The pregnancy changed. Refresh it.", 412)
        previous = None
        if supersedes is None:
            if episode.lifecycle != "ongoing" or episode.completion_record_id is not None:
                return _error("conflict", "Initial completion requires an ongoing pregnancy.", 409)
        else:
            if episode.lifecycle != "completed" or episode.completion_record_id != supersedes:
                return _error(
                    "conflict", "A correction must supersede the current completion record.", 409
                )
            previous = db.session.get(PregnancyCompletionRecord, supersedes)
            if previous is None:
                return _deny(context, "pregnancy.complete")
        now = db.session.scalar(select(sa.func.clock_timestamp()))
        responsibility = db.session.scalar(
            select(EpisodeFacilityAssignment).where(
                EpisodeFacilityAssignment.episode_id == episode_id,
                EpisodeFacilityAssignment.facility_id == facility,
                EpisodeFacilityAssignment.valid_from <= now,
                sa.or_(
                    EpisodeFacilityAssignment.valid_to.is_(None),
                    EpisodeFacilityAssignment.valid_to > now,
                ),
            )
        )
        if responsibility is None:
            return _deny(context, "pregnancy.complete")
        bookings = (
            []
            if previous
            else db.session.scalars(
                select(Appointment)
                .where(
                    Appointment.episode_facility_assignment_id == responsibility.id,
                    Appointment.facility_id == facility,
                    Appointment.episode_id == episode_id,
                    Appointment.status.in_(["scheduled", "confirmed"]),
                    Appointment.starts_at >= now,
                )
                .order_by(Appointment.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
        )
        record_id = uuid4()
        version = episode.version + 1
        closed_at = previous.closed_at if previous else now
        communication_state = (
            "withdrawn" if episode.communication_state == "withdrawn" else "completed"
        )
        data = {
            "id": str(record_id),
            "episode_id": str(episode_id),
            "patient_id": str(episode.patient_id),
            "facility_id": str(facility),
            "episode_facility_assignment_id": str(responsibility.id),
            "completed_on": completed_on,
            "approved_outcome_fields": {},
            "reason": reason,
            "recorded_by": str(context.user.id),
            "recorded_at": _iso(now),
            "closed_at": _iso(closed_at),
            "supersedes_id": str(supersedes) if supersedes else None,
            "sequence": previous.sequence + 1 if previous else 1,
            "parent_version": version,
            "cancelled_appointment_count": len(bookings),
            "lifecycle": "completed",
            "communication_state": communication_state,
            "messaging_eligible": False,
        }
        record = PregnancyCompletionRecord(
            id=record_id,
            episode_id=episode_id,
            patient_id=episode.patient_id,
            facility_id=facility,
            episode_facility_assignment_id=responsibility.id,
            completed_on=completed_date,
            approved_fields_cipher=_crypt({}, "pregnancy-completion-outcomes"),
            reason_cipher=_crypt({"reason": reason}, "pregnancy-completion-reason"),
            response_cipher=_crypt(data, "pregnancy-completion-response"),
            key_version=current_app.config["PATIENT_KEY_VERSION"],
            recorded_by=context.user.id,
            recorded_at=now,
            closed_at=closed_at,
            supersedes_id=supersedes,
            sequence=data["sequence"],
            parent_version=version,
            cancelled_appointment_count=len(bookings),
            idempotency_key=key,
            request_hash=fingerprint,
        )
        db.session.add(record)
        db.session.flush()
        for booking in bookings:
            old_status = booking.status
            booking.status, booking.version = "cancelled", booking.version + 1
            _event(
                booking,
                context.user.id,
                previous=old_status,
                reason="Pregnancy lifecycle completed.",
                effective=now,
                completion_id=record.id,
            )
            _appointment_audit(
                context, "pregnancy.complete", "appointment.completion_cancel", facility, booking.id
            )
        db.session.flush()
        episode.lifecycle, episode.communication_state = "completed", communication_state
        episode.completion_record_id, episode.version = record.id, version
        db.session.flush()
        _record_pregnancy_audit(
            context=context,
            permission="pregnancy.complete",
            action="pregnancy.completion.correct" if previous else "pregnancy.complete",
            facility_id=facility,
            episode_id=episode_id,
            metadata={"state": "completed"},
        )
        db.session.commit()
        return _response(data)
    except IntegrityError:
        db.session.rollback()
        return _error("conflict", "The completion request conflicts with existing history.", 409)
    except SQLAlchemyError:
        return _unavailable()


@blueprint.get("/<uuid:episode_id>/completion")
@require_permission("pregnancy.complete")
def completion_history(episode_id):
    context = get_access_context()
    facility = _parse_uuid(request.args.get("facility_id"))
    if facility is None:
        return _error("validation_failed", "A valid facility context is required.", 422)
    try:
        if not _scope(context, facility, "pregnancy.complete"):
            return _deny(context, "pregnancy.complete")
        establish_patient_context(context, facility)
        episode = _episode(episode_id, facility)
        if episode is None:
            return _deny(context, "pregnancy.complete")
        establish_evidence_patient_context(episode.patient_id)
        records = db.session.scalars(
            select(PregnancyCompletionRecord)
            .where(PregnancyCompletionRecord.episode_id == episode_id)
            .order_by(PregnancyCompletionRecord.sequence.desc())
        ).all()
        data = [
            _decrypt(row.response_cipher, row.key_version, "pregnancy-completion-response")
            for row in records
        ]
        version = episode.version
        _record_pregnancy_audit(
            context=context,
            permission="pregnancy.complete",
            action="pregnancy.completion.history",
            facility_id=facility,
            episode_id=episode_id,
        )
        db.session.commit()
        response = jsonify(data=data)
        response.headers["ETag"] = f'"{version}"'
        return response, 200
    except SQLAlchemyError:
        return _unavailable()
