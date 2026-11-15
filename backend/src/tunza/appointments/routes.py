"""Staff-entered bookings only: no clinical schedule generation or messaging."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from flask import current_app, g, jsonify, request
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from tunza.access import get_access_context, require_permission
from tunza.appointments import blueprint
from tunza.appointments.models import STATES, Appointment, AppointmentEvent, AppointmentIdempotency
from tunza.audit import record_audit
from tunza.extensions import db
from tunza.patients.models import EpisodeFacilityAssignment, PregnancyEpisode
from tunza.patients.routes import _advisory_lock_key, _parse_uuid, _validate_idempotency_key
from tunza.patients.security import establish_evidence_patient_context, establish_patient_context
from tunza.pregnancy.routes import _scope
from tunza.services.encryption import decrypt_json, encrypt_json

PERMISSIONS = {
    "schedule": "appointment.schedule",
    "confirm": "appointment.confirm",
    "reschedule": "appointment.reschedule",
    "cancel": "appointment.cancel",
    "attendance": "appointment.attend",
}


def _error(code, message, status):
    return jsonify(error={"code": code, "message": message, "request_id": g.request_id}), status


def _unavailable():
    db.session.rollback()
    return _error("service_unavailable", "The request could not be completed.", 503)


def _audit(context, permission, action, facility, resource=None, outcome="success"):
    record_audit(
        permission=permission,
        action=action,
        resource_type="appointment",
        outcome=outcome,
        actor_id=context.user.id,
        facility_id=facility,
        resource_id=resource,
        request_id=g.request_id,
    )


def _deny(context, permission):
    _audit(context, permission, "appointment.access", None, outcome="denied")
    db.session.commit()
    return _error("not_found", "The request could not be completed.", 404)


def _utc(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", value
    ):
        raise ValueError("A timezone-aware RFC 3339 timestamp is required.")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except (ValueError, OverflowError):
        raise ValueError("A valid timezone-aware timestamp is required.") from None


def _iso(value):
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None


def reminder_hold_reason(row, *, ongoing, replacement_id=None, now=None):
    """Fail closed: even a confirmed future booking needs approved D06 policy."""
    if row.status == "rescheduled" or replacement_id:
        return "superseded"
    if row.status != "confirmed":
        return row.status
    if not ongoing:
        return "episode_completed"
    if row.starts_at <= (now or datetime.now(UTC)):
        return "appointment_not_future"
    return "D06_unconfigured"


def _projection(row, episode):
    replacement = db.session.scalar(
        select(Appointment.id).where(Appointment.supersedes_appointment_id == row.id)
    )
    return {
        "id": str(row.id),
        "episode_id": str(row.episode_id),
        "patient_id": str(row.patient_id),
        "facility_id": str(row.facility_id),
        "episode_facility_assignment_id": str(row.episode_facility_assignment_id),
        "starts_at": _iso(row.starts_at),
        "status": row.status,
        "version": row.version,
        "episode_version": episode.version,
        "confirmed_by": str(row.confirmed_by) if row.confirmed_by else None,
        "confirmed_at": _iso(row.confirmed_at),
        "attendance_effective_at": _iso(row.attendance_effective_at),
        "supersedes_id": str(row.supersedes_appointment_id)
        if row.supersedes_appointment_id
        else None,
        "superseded_by_id": str(replacement) if replacement else None,
        "reminder_eligible": False,
        "reminder_hold_reason": reminder_hold_reason(
            row, ongoing=episode.lifecycle == "ongoing", replacement_id=replacement
        ),
    }


def _response(data, status=200):
    response = jsonify(data=data)
    if isinstance(data, dict) and "version" in data:
        response.headers["ETag"] = f'"{data["version"]}"'
        if status == 201:
            response.headers["Location"] = (
                f"/api/v1/appointments/{data['id']}?facility_id={data['facility_id']}"
            )
    return response, status


def _crypt(value, purpose, *, version=None):
    return encrypt_json(
        value,
        key=current_app.config["PATIENT_ENCRYPTION_KEY"],
        key_version=version or current_app.config["PATIENT_KEY_VERSION"],
        purpose=purpose,
    )


def _decrypt(value, version, purpose):
    return decrypt_json(
        value,
        key=current_app.config["PATIENT_ENCRYPTION_KEY"],
        key_version=version,
        purpose=purpose,
    )


def _event(row, actor, *, previous=None, reason=None, related_id=None, effective=None):
    db.session.add(
        AppointmentEvent(
            id=uuid4(),
            appointment_id=row.id,
            episode_id=row.episode_id,
            patient_id=row.patient_id,
            facility_id=row.facility_id,
            event_type=row.status,
            sequence=row.version,
            recorded_by=actor,
            effective_at=effective or datetime.now(UTC),
            key_version=current_app.config["PATIENT_KEY_VERSION"],
            event_data_cipher=_crypt(
                {
                    "from_status": previous,
                    "to_status": row.status,
                    "starts_at": _iso(row.starts_at),
                    "reason": reason,
                    "related_appointment_id": str(related_id) if related_id else None,
                },
                "appointment-event",
            ),
        )
    )


def _payload(action):
    payload = request.get_json(silent=True)
    allowed = {
        "schedule": {"episode_facility_assignment_id", "starts_at", "reference_contact_id"},
        "confirm": {"reason"},
        "reschedule": {"starts_at", "reason"},
        "cancel": {"reason"},
        "attendance": {"status", "effective_at", "reason"},
    }[action]
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("Request validation failed.")
    result = {}
    reason = payload.get("reason")
    if reason is not None:
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 1000:
            raise ValueError("Request validation failed.")
        result["reason"] = reason.strip()
    if action in {"cancel", "reschedule"} and reason is None:
        raise ValueError("A reason is required.")
    if action in {"schedule", "reschedule"}:
        result["starts_at"] = _iso(_utc(payload.get("starts_at")))
    if action == "schedule":
        assignment = _parse_uuid(payload.get("episode_facility_assignment_id"))
        if assignment is None or payload.get("reference_contact_id") is not None:
            raise ValueError(
                "An assignment is required; clinical reference schedules remain unconfigured."
            )
        result["episode_facility_assignment_id"] = str(assignment)
    if action == "attendance":
        if not isinstance(payload.get("status"), str) or payload["status"] not in {
            "attended",
            "missed",
        }:
            raise ValueError("Staff must explicitly record attended or missed.")
        result.update(
            status=payload["status"], effective_at=_iso(_utc(payload.get("effective_at")))
        )
    return result


def _write(action, appointment_id=None):
    context = get_access_context()
    permission = PERMISSIONS[action]
    facility = _parse_uuid(request.args.get("facility_id"))
    if facility is None:
        return _error("validation_failed", "A valid facility context is required.", 422)
    try:
        if not _scope(context, facility, permission):
            return _deny(context, permission)
        establish_patient_context(context, facility)
        # Resolve only rows independently authorized by pregnancy/appointment RLS.
        if action == "schedule":
            try:
                body = _payload(action)
            except ValueError as error:
                return _error("validation_failed", str(error), 422)
            assignment = db.session.scalar(
                select(EpisodeFacilityAssignment)
                .join(PregnancyEpisode, PregnancyEpisode.id == EpisodeFacilityAssignment.episode_id)
                .where(
                    EpisodeFacilityAssignment.id == UUID(body["episode_facility_assignment_id"]),
                    EpisodeFacilityAssignment.facility_id == facility,
                    EpisodeFacilityAssignment.valid_from <= sa.func.statement_timestamp(),
                    sa.or_(
                        EpisodeFacilityAssignment.valid_to.is_(None),
                        EpisodeFacilityAssignment.valid_to > sa.func.statement_timestamp(),
                    ),
                )
            )
            if assignment is None:
                return _deny(context, permission)
            episode_id, patient_id = assignment.episode_id, assignment.patient_id
        else:
            row = db.session.scalar(
                select(Appointment).where(
                    Appointment.id == appointment_id, Appointment.facility_id == facility
                )
            )
            if row is None:
                return _deny(context, permission)
            episode_id, patient_id = row.episode_id, row.patient_id
            try:
                body = _payload(action)
            except ValueError as error:
                return _error("validation_failed", str(error), 422)
        establish_evidence_patient_context(patient_id)
        key = _validate_idempotency_key()
        if key is None:
            return _error("validation_failed", "A valid Idempotency-Key is required.", 422)
        operation = "appointment." + action
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "actor": str(context.user.id),
                    "operation": operation,
                    "facility": str(facility),
                    "patient": str(patient_id),
                    "episode": str(episode_id),
                    "target": str(appointment_id) if appointment_id else None,
                    "body": body,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).digest()
        db.session.execute(
            sa.text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": _advisory_lock_key(f"{operation}:{key}", context.user.id, context.user.id)},
        )
        command = db.session.scalar(
            select(AppointmentIdempotency).where(
                AppointmentIdempotency.actor_id == context.user.id,
                AppointmentIdempotency.operation == operation,
                AppointmentIdempotency.key == key,
            )
        )
        if command is not None:
            if command.request_hash != fingerprint:
                db.session.rollback()
                return _error("conflict", "The idempotency key was already used.", 409)
            data = _decrypt(command.response_cipher, command.key_version, "appointment-response")
            status = command.response_status
            _audit(context, permission, "appointment.replay", facility, command.appointment_id)
            db.session.commit()
            return _response(data, status)
        # Consistent locking order: command key, pregnancy, then booking.
        episode = db.session.scalar(
            select(PregnancyEpisode)
            .where(PregnancyEpisode.id == episode_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if episode is None:
            return _deny(context, permission)
        if action != "schedule":
            row = db.session.scalar(
                select(Appointment)
                .where(Appointment.id == appointment_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if row is None:
                return _deny(context, permission)
        match = request.headers.get("If-Match")
        if match is None:
            return _error("precondition_required", "An If-Match version is required.", 428)
        if match != f'"{episode.version if action == "schedule" else row.version}"':
            return _error(
                "precondition_failed", "The record changed. Refresh before retrying.", 412
            )
        if action != "cancel" and episode.lifecycle != "ongoing":
            return _error("conflict", "The pregnancy episode is not ongoing.", 409)
        if action == "schedule":
            row = Appointment(
                id=uuid4(),
                episode_id=episode_id,
                patient_id=patient_id,
                facility_id=facility,
                episode_facility_assignment_id=assignment.id,
                starts_at=_utc(body["starts_at"]),
                status="scheduled",
                version=1,
                created_by=context.user.id,
            )
            db.session.add(row)
            episode.version += 1
            db.session.flush()
            _event(row, context.user.id)
            target = row
        else:
            allowed = {
                "confirm": {"scheduled"},
                "reschedule": {"scheduled", "confirmed"},
                "cancel": {"scheduled", "confirmed"},
                "attendance": {"confirmed"},
            }[action]
            if row.status not in allowed:
                return _error("conflict", "The appointment state does not allow this action.", 409)
            previous = row.status
            row.version += 1
            if action == "reschedule":
                if row.starts_at == _utc(body["starts_at"]):
                    db.session.rollback()
                    return _error("conflict", "A reschedule must change the appointment time.", 409)
                row.status = "rescheduled"
                db.session.flush()  # release the old active-slot constraint before replacement
                target = Appointment(
                    id=uuid4(),
                    episode_id=episode_id,
                    patient_id=patient_id,
                    facility_id=facility,
                    episode_facility_assignment_id=row.episode_facility_assignment_id,
                    starts_at=_utc(body["starts_at"]),
                    status="scheduled",
                    version=1,
                    created_by=context.user.id,
                    supersedes_appointment_id=row.id,
                )
                db.session.add(target)
                episode.version += 1
                db.session.flush()
                _event(
                    row,
                    context.user.id,
                    previous=previous,
                    reason=body["reason"],
                    related_id=target.id,
                )
                _event(target, context.user.id, reason=body["reason"], related_id=row.id)
            else:
                row.status = {
                    "confirm": "confirmed",
                    "cancel": "cancelled",
                    "attendance": body.get("status"),
                }[action]
                if action == "confirm":
                    row.confirmed_by, row.confirmed_at = context.user.id, datetime.now(UTC)
                if action == "attendance":
                    row.attendance_effective_at = _utc(body["effective_at"])
                db.session.flush()
                _event(
                    row,
                    context.user.id,
                    previous=previous,
                    reason=body.get("reason"),
                    effective=row.attendance_effective_at if action == "attendance" else None,
                )
                target = row
        db.session.flush()
        data = _projection(target, episode)
        status = 201 if action in {"schedule", "reschedule"} else 200
        db.session.add(
            AppointmentIdempotency(
                id=uuid4(),
                actor_id=context.user.id,
                operation=operation,
                key=key,
                appointment_id=row.id,
                episode_id=episode_id,
                patient_id=patient_id,
                facility_id=facility,
                request_hash=fingerprint,
                response_cipher=_crypt(data, "appointment-response"),
                response_status=status,
                key_version=current_app.config["PATIENT_KEY_VERSION"],
            )
        )
        _audit(context, permission, operation, facility, row.id)
        db.session.commit()
        return _response(data, status)
    except IntegrityError:
        db.session.rollback()
        return _error(
            "conflict", "The booking or idempotency key conflicts with an existing record.", 409
        )
    except SQLAlchemyError:
        return _unavailable()


@blueprint.post("")
@require_permission("appointment.schedule")
def schedule():
    return _write("schedule")


@blueprint.post("/<uuid:appointment_id>/confirm")
@require_permission("appointment.confirm")
def confirm(appointment_id):
    return _write("confirm", appointment_id)


@blueprint.post("/<uuid:appointment_id>/reschedule")
@require_permission("appointment.reschedule")
def reschedule(appointment_id):
    return _write("reschedule", appointment_id)


@blueprint.post("/<uuid:appointment_id>/cancel")
@require_permission("appointment.cancel")
def cancel(appointment_id):
    return _write("cancel", appointment_id)


@blueprint.post("/<uuid:appointment_id>/attendance")
@require_permission("appointment.attend")
def attendance(appointment_id):
    return _write("attendance", appointment_id)


def _read(appointment_id, history=False):
    context = get_access_context()
    facility = _parse_uuid(request.args.get("facility_id"))
    if facility is None:
        return _error("validation_failed", "A valid facility context is required.", 422)
    try:
        if not _scope(context, facility, "appointment.read"):
            return _deny(context, "appointment.read")
        establish_patient_context(context, facility)
        row = db.session.scalar(
            select(Appointment).where(
                Appointment.id == appointment_id, Appointment.facility_id == facility
            )
        )
        if row is None:
            return _deny(context, "appointment.read")
        establish_evidence_patient_context(row.patient_id)
        episode = db.session.get(PregnancyEpisode, row.episode_id)
        if history:
            events = db.session.scalars(
                select(AppointmentEvent)
                .where(AppointmentEvent.appointment_id == row.id)
                .order_by(AppointmentEvent.sequence)
            ).all()
            data = [
                {
                    "id": str(event.id),
                    "event_type": event.event_type,
                    "sequence": event.sequence,
                    "recorded_by": str(event.recorded_by),
                    "effective_at": _iso(event.effective_at),
                    "created_at": _iso(event.created_at),
                    "data": _decrypt(
                        event.event_data_cipher, event.key_version, "appointment-event"
                    ),
                }
                for event in events
            ]
        else:
            data = _projection(row, episode)
        _audit(
            context,
            "appointment.read",
            "appointment.history" if history else "appointment.read",
            facility,
            row.id,
        )
        db.session.commit()
        return _response(data)
    except SQLAlchemyError:
        return _unavailable()


@blueprint.get("/<uuid:appointment_id>")
@require_permission("appointment.read")
def detail(appointment_id):
    return _read(appointment_id)


@blueprint.get("/<uuid:appointment_id>/events")
@require_permission("appointment.read")
def history(appointment_id):
    return _read(appointment_id, history=True)


@blueprint.get("")
@require_permission("appointment.read")
def list_appointments():
    context = get_access_context()
    facility = _parse_uuid(request.args.get("facility_id"))
    episode_id = _parse_uuid(request.args.get("episode_id"))
    if facility is None or episode_id is None:
        return _error("validation_failed", "Valid facility and episode IDs are required.", 422)
    try:
        if not _scope(context, facility, "appointment.read"):
            return _deny(context, "appointment.read")
        establish_patient_context(context, facility)
        episode = db.session.get(PregnancyEpisode, episode_id)
        if episode is None:
            return _deny(context, "appointment.read")
        establish_evidence_patient_context(episode.patient_id)
        query = select(Appointment).where(
            Appointment.episode_id == episode_id, Appointment.facility_id == facility
        )
        try:
            limit = int(request.args.get("limit", "25"))
            if not 1 <= limit <= 100:
                raise ValueError()
            status = request.args.get("status")
            if status:
                if status not in STATES:
                    raise ValueError()
                query = query.where(Appointment.status == status)
            start, end = request.args.get("from"), request.args.get("to")
            if start:
                query = query.where(Appointment.starts_at >= _utc(start))
            if end:
                query = query.where(Appointment.starts_at < _utc(end))
            if start and end and _utc(start) >= _utc(end):
                raise ValueError()
            binding = [str(context.user.id), str(facility), str(episode_id), status, start, end]
            serializer = URLSafeSerializer(
                current_app.config["SECRET_KEY"], salt="appointment-cursor"
            )
            cursor = request.args.get("cursor")
            if cursor:
                decoded = serializer.loads(cursor)
                if decoded["scope"] != binding:
                    raise ValueError()
                query = query.where(
                    sa.tuple_(Appointment.starts_at, Appointment.id)
                    > sa.tuple_(_utc(decoded["time"]), UUID(decoded["id"]))
                )
        except (ValueError, BadSignature, KeyError, TypeError):
            return _error("validation_failed", "Invalid list filters or cursor.", 422)
        rows = db.session.scalars(
            query.order_by(Appointment.starts_at, Appointment.id).limit(limit + 1)
        ).all()
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = serializer.dumps(
                {"scope": binding, "time": _iso(last.starts_at), "id": str(last.id)}
            )
        data = [_projection(row, episode) for row in rows[:limit]]
        _audit(context, "appointment.read", "appointment.list", facility, episode_id)
        db.session.commit()
        return jsonify(data=data, page={"next_cursor": next_cursor}), 200
    except SQLAlchemyError:
        return _unavailable()
