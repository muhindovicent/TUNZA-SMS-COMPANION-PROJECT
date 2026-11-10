"""Synthetic-only patient registration and assignment-scoped read workflows."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

from flask import Response, current_app, g, jsonify, request
from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from tunza.access import facility_scope_allows, get_access_context, require_permission
from tunza.audit import record_audit
from tunza.extensions import db
from tunza.identity.models import FacilityMembership
from tunza.patients import blueprint
from tunza.patients.models import (
    CommunicationPreference,
    EpisodeFacilityAssignment,
    FacilityPatientProfile,
    ParticipantAssignment,
    Patient,
    PregnancyEpisode,
    RegistrationIdempotency,
)
from tunza.patients.security import establish_patient_context
from tunza.services.encryption import decrypt_json, encrypt_json

SYNTHETIC_REFERENCE = re.compile(r"^SYN-ADULT-[A-Z0-9][A-Z0-9-]{2,53}$")
ALLOWED_LANGUAGES = frozenset({"en", "lg"})
MAX_IDEMPOTENCY_KEY_LENGTH = 128
UNKNOWN_FIELD_MESSAGE = "Request validation failed."


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


def _active_membership(context, facility_id: UUID) -> FacilityMembership | None:
    return next(
        (membership for membership in context.memberships if membership.facility_id == facility_id),
        None,
    )


def _validate_idempotency_key() -> str | None:
    value = request.headers.get("Idempotency-Key")
    if not value or len(value) > MAX_IDEMPOTENCY_KEY_LENGTH:
        return None
    if any(ord(character) < 33 or ord(character) > 126 for character in value):
        return None
    return value


def _patient_projection(
    profile: FacilityPatientProfile,
    preference: CommunicationPreference,
) -> dict[str, object]:
    demographics = decrypt_json(
        profile.demographics_cipher,
        key=current_app.config["PATIENT_ENCRYPTION_KEY"],
        key_version=profile.key_version,
        purpose="patient-demographics",
    )
    return {
        "id": str(profile.patient_id),
        "facility_id": str(profile.facility_id),
        "local_reference": profile.local_reference,
        "demographics": demographics,
        "language": preference.language,
        "version": profile.version,
    }


def _assigned_profiles(context, facility_id: UUID):
    membership_ids = [
        membership.id for membership in context.memberships if membership.facility_id == facility_id
    ]
    if not membership_ids:
        return select(FacilityPatientProfile).where(False)
    now = datetime.now(UTC)
    return (
        select(FacilityPatientProfile)
        .join(
            EpisodeFacilityAssignment,
            and_(
                EpisodeFacilityAssignment.patient_id == FacilityPatientProfile.patient_id,
                EpisodeFacilityAssignment.facility_id == FacilityPatientProfile.facility_id,
            ),
        )
        .join(
            ParticipantAssignment,
            and_(
                ParticipantAssignment.episode_facility_assignment_id
                == EpisodeFacilityAssignment.id,
                ParticipantAssignment.facility_id == EpisodeFacilityAssignment.facility_id,
            ),
        )
        .where(
            FacilityPatientProfile.facility_id == facility_id,
            EpisodeFacilityAssignment.valid_from <= now,
            or_(
                EpisodeFacilityAssignment.valid_to.is_(None),
                EpisodeFacilityAssignment.valid_to > now,
            ),
            ParticipantAssignment.membership_id.in_(membership_ids),
            ParticipantAssignment.valid_from <= now,
            or_(
                ParticipantAssignment.valid_to.is_(None),
                ParticipantAssignment.valid_to > now,
            ),
        )
        .distinct()
    )


def _record_patient_audit(
    *, context, permission: str, action: str, facility_id: UUID, patient_id: UUID | None
) -> None:
    record_audit(
        permission=permission,
        action=action,
        resource_type="patient",
        outcome="success",
        request_id=g.request_id,
        actor_id=context.user.id,
        facility_id=facility_id,
        resource_id=patient_id,
        metadata={"method": "local_reference"} if action == "patient.search" else None,
    )


def _deny_patient_access(context, permission: str, reason: str) -> tuple[Response, int]:
    try:
        record_audit(
            permission=permission,
            action="patient.access",
            resource_type="patient",
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


@blueprint.post("")
@require_permission("patient.register")
def register_patient() -> tuple[Response, int]:
    context = get_access_context()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error("validation_failed", "A JSON request body is required.", 422)
    if set(payload) - {"facility_id", "local_reference", "demographics", "language"}:
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
    idempotency_key = _validate_idempotency_key()
    if idempotency_key is None:
        return _error("validation_failed", "A valid Idempotency-Key is required.", 422)

    facility_id = _parse_uuid(payload.get("facility_id"))
    reference_value = payload.get("local_reference")
    demographics = payload.get("demographics")
    language = payload.get("language")
    if (
        facility_id is None
        or not isinstance(reference_value, str)
        or not isinstance(language, str)
        or demographics != {}
    ):
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
    local_reference = reference_value.strip().upper()
    if not SYNTHETIC_REFERENCE.fullmatch(local_reference) or language not in ALLOWED_LANGUAGES:
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
    if not facility_scope_allows(context, facility_id, "patient.register"):
        return _deny_patient_access(context, "patient.register", "facility_scope")
    membership = _active_membership(context, facility_id)
    if membership is None:
        return _deny_patient_access(context, "patient.register", "facility_scope")

    establish_patient_context(context, facility_id)
    request_hash = hashlib.sha256(
        json.dumps(
            {
                "facility_id": str(facility_id),
                "local_reference": local_reference,
                "demographics": demographics,
                "language": language,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).digest()
    now = datetime.now(UTC)
    patient = Patient(id=uuid4())
    profile = FacilityPatientProfile(
        id=uuid4(),
        patient_id=patient.id,
        facility_id=facility_id,
        local_reference=local_reference,
        demographics_cipher=encrypt_json(
            {},
            key=current_app.config["PATIENT_ENCRYPTION_KEY"],
            key_version=current_app.config["PATIENT_KEY_VERSION"],
            purpose="patient-demographics",
        ),
        key_version=current_app.config["PATIENT_KEY_VERSION"],
        status="synthetic",
    )
    preference = CommunicationPreference(
        id=uuid4(), patient_id=patient.id, facility_id=facility_id, language=language
    )
    episode = PregnancyEpisode(
        id=uuid4(), patient_id=patient.id, lifecycle="ongoing", communication_state="paused"
    )
    facility_assignment = EpisodeFacilityAssignment(
        id=uuid4(),
        episode_id=episode.id,
        patient_id=patient.id,
        facility_id=facility_id,
        valid_from=now,
    )
    participant_assignment = ParticipantAssignment(
        id=uuid4(),
        episode_facility_assignment_id=facility_assignment.id,
        facility_id=facility_id,
        membership_id=membership.id,
        duty="primary",
        valid_from=now,
    )
    try:
        record_id = uuid4()
        inserted = db.session.scalar(
            insert(RegistrationIdempotency)
            .values(
                id=record_id,
                actor_id=context.user.id,
                facility_id=facility_id,
                key=idempotency_key,
                request_hash=request_hash,
                key_version=current_app.config["PATIENT_KEY_VERSION"],
            )
            .on_conflict_do_nothing(index_elements=["actor_id", "key"])
            .returning(RegistrationIdempotency.id)
        )
        if inserted is None:
            existing = db.session.scalar(
                select(RegistrationIdempotency).where(
                    RegistrationIdempotency.actor_id == context.user.id,
                    RegistrationIdempotency.key == idempotency_key,
                )
            )
            if existing is None or existing.request_hash != request_hash:
                db.session.rollback()
                return _error("conflict", "The idempotency key was already used.", 409)
            if existing.response_cipher is None:
                db.session.rollback()
                return _error("request_in_progress", "The request is in progress.", 409)
            result = decrypt_json(
                existing.response_cipher,
                key=current_app.config["PATIENT_ENCRYPTION_KEY"],
                key_version=existing.key_version,
                purpose="patient-registration-response",
            )
            replay_patient_id = UUID(result["patient"]["id"])
            if (
                db.session.scalar(
                    _assigned_profiles(context, facility_id).where(
                        FacilityPatientProfile.patient_id == replay_patient_id
                    )
                )
                is None
            ):
                return _deny_patient_access(context, "patient.register", "unavailable")
            _record_patient_audit(
                context=context,
                permission="patient.register",
                action="patient.read",
                facility_id=facility_id,
                patient_id=replay_patient_id,
            )
            db.session.commit()
            response = jsonify(data=result)
            response.headers["Location"] = (
                f"/api/v1/patients/{replay_patient_id}?facility_id={facility_id}"
            )
            return response, 201
        # These models have no ORM relationships, so order the FK-dependent
        # inserts explicitly while retaining a single atomic transaction.
        for row in (
            patient,
            profile,
            preference,
            episode,
            facility_assignment,
            participant_assignment,
        ):
            db.session.add(row)
            db.session.flush()
        _record_patient_audit(
            context=context,
            permission="patient.register",
            action="patient.register",
            facility_id=facility_id,
            patient_id=patient.id,
        )
        db.session.flush()
        response_data = {
            "patient": _patient_projection(profile, preference),
            "pregnancy": {
                "id": str(episode.id),
                "patient_id": str(patient.id),
                "lifecycle": episode.lifecycle,
                "communication_state": episode.communication_state,
                "version": episode.version,
            },
            "participant_assignment_id": str(participant_assignment.id),
        }
        patient_location = f"/api/v1/patients/{patient.id}?facility_id={facility_id}"
        record = db.session.get(RegistrationIdempotency, record_id)
        record.response_cipher = encrypt_json(
            response_data,
            key=current_app.config["PATIENT_ENCRYPTION_KEY"],
            key_version=record.key_version,
            purpose="patient-registration-response",
        )
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _error("conflict", "The synthetic patient reference is already registered.", 409)
    except SQLAlchemyError:
        db.session.rollback()
        return _service_unavailable()

    response = jsonify(data=response_data)
    response.headers["Location"] = patient_location
    return response, 201


@blueprint.post("/search")
@require_permission("patient.read")
def search_patients() -> tuple[Response, int]:
    context = get_access_context()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error("validation_failed", "A JSON request body is required.", 422)
    if set(payload) - {"facility_id", "local_reference", "limit"}:
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
    facility_id = _parse_uuid(payload.get("facility_id"))
    reference_value = payload.get("local_reference")
    limit = payload.get("limit", 25)
    if (
        facility_id is None
        or not isinstance(reference_value, str)
        or not isinstance(limit, int)
        or isinstance(limit, bool)
        or not 1 <= limit <= 100
    ):
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
    local_reference = reference_value.strip().upper()
    if not SYNTHETIC_REFERENCE.fullmatch(local_reference):
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
    if not facility_scope_allows(context, facility_id, "patient.read"):
        return _deny_patient_access(context, "patient.read", "facility_scope")

    establish_patient_context(context, facility_id)
    profiles = list(
        db.session.scalars(
            _assigned_profiles(context, facility_id)
            .where(FacilityPatientProfile.local_reference == local_reference)
            .limit(limit)
        ).all()
    )
    preference_by_patient = {
        preference.patient_id: preference
        for preference in db.session.scalars(
            select(CommunicationPreference).where(
                CommunicationPreference.facility_id == facility_id,
                CommunicationPreference.patient_id.in_(
                    [profile.patient_id for profile in profiles]
                ),
            )
        ).all()
    }
    data = [
        _patient_projection(profile, preference_by_patient[profile.patient_id])
        for profile in profiles
    ]
    try:
        _record_patient_audit(
            context=context,
            permission="patient.read",
            action="patient.search",
            facility_id=facility_id,
            patient_id=profiles[0].patient_id if len(profiles) == 1 else None,
        )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return _service_unavailable()
    return jsonify(data=data, page={"next_cursor": None}), 200


@blueprint.get("/<uuid:patient_id>")
@require_permission("patient.read")
def patient_detail(patient_id: UUID) -> tuple[Response, int]:
    context = get_access_context()
    facility_id = _parse_uuid(request.args.get("facility_id"))
    if facility_id is None:
        return _error("validation_failed", "A valid facility context is required.", 422)
    if not facility_scope_allows(context, facility_id, "patient.read"):
        return _deny_patient_access(context, "patient.read", "facility_scope")

    establish_patient_context(context, facility_id)
    profile = db.session.scalar(
        _assigned_profiles(context, facility_id).where(
            FacilityPatientProfile.patient_id == patient_id
        )
    )
    if profile is None:
        return _deny_patient_access(context, "patient.read", "unavailable")
    preference = db.session.scalar(
        select(CommunicationPreference).where(
            CommunicationPreference.patient_id == patient_id,
            CommunicationPreference.facility_id == facility_id,
        )
    )
    if preference is None:
        return _deny_patient_access(context, "patient.read", "unavailable")
    data = _patient_projection(profile, preference)
    version = profile.version
    try:
        _record_patient_audit(
            context=context,
            permission="patient.read",
            action="patient.read",
            facility_id=facility_id,
            patient_id=patient_id,
        )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return _service_unavailable()
    response = jsonify(data=data)
    response.headers["ETag"] = f'"{version}"'
    return response, 200
