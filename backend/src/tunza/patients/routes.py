"""Synthetic-only patient registration and assignment-scoped read workflows."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
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
    AdultEligibilityCheck,
    CommunicationPreference,
    ConsentEvent,
    ConsentNoticeVersion,
    EpisodeFacilityAssignment,
    FacilityPatientProfile,
    ParticipantAssignment,
    Patient,
    PregnancyEpisode,
    ProtectedEvidence,
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


def _advisory_lock_key(namespace: str, patient_id: UUID, facility_id: UUID) -> int:
    """Deterministic transaction-scoped advisory lock key (stable across processes)."""
    digest = hashlib.sha256(f"{namespace}:{patient_id}:{facility_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % (2**31)


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
    *,
    context,
    permission: str,
    action: str,
    facility_id: UUID,
    patient_id: UUID | None,
    metadata: dict[str, object] | None = None,
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
        metadata=metadata
        or ({"method": "local_reference"} if action == "patient.search" else None),
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


def _eligibility_projection(check: AdultEligibilityCheck) -> dict[str, object]:
    return {
        "id": str(check.id),
        "patient_id": str(check.patient_id),
        "facility_id": str(check.facility_id),
        "method": check.method,
        "result": check.result,
        "checked_at": check.checked_at.isoformat(),
        "sequence": check.sequence,
        "version": check.version,
    }


@blueprint.get("/<uuid:patient_id>/eligibility")
@require_permission("eligibility.record")
def list_eligibility_checks(patient_id: UUID) -> tuple[Response, int]:
    context = get_access_context()
    facility_id = _parse_uuid(request.args.get("facility_id"))
    if facility_id is None:
        return _error("validation_failed", "A valid facility context is required.", 422)
    if not facility_scope_allows(context, facility_id, "eligibility.record"):
        return _deny_patient_access(context, "eligibility.record", "facility_scope")
    membership = _active_membership(context, facility_id)
    if membership is None:
        return _deny_patient_access(context, "eligibility.record", "facility_scope")
    establish_patient_context(context, facility_id)
    profile = db.session.scalar(
        _assigned_profiles(context, facility_id).where(
            FacilityPatientProfile.patient_id == patient_id
        )
    )
    if profile is None:
        return _deny_patient_access(context, "eligibility.record", "unavailable")
    checks = list(
        db.session.scalars(
            select(AdultEligibilityCheck)
            .where(
                AdultEligibilityCheck.patient_id == patient_id,
                AdultEligibilityCheck.facility_id == facility_id,
            )
            .order_by(AdultEligibilityCheck.checked_at.desc())
        ).all()
    )
    try:
        _record_patient_audit(
            context=context,
            permission="eligibility.record",
            action="eligibility.history",
            facility_id=facility_id,
            patient_id=patient_id,
        )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return _service_unavailable()
    return jsonify(data=[_eligibility_projection(c) for c in checks]), 200


@blueprint.post("/<uuid:patient_id>/eligibility")
@require_permission("eligibility.record")
def record_eligibility(patient_id: UUID) -> tuple[Response, int]:
    context = get_access_context()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error("validation_failed", "A JSON request body is required.", 422)
    if set(payload) - {"method", "result", "checked_at", "evidence_id"}:
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
    method = payload.get("method")
    result = payload.get("result")
    checked_at = payload.get("checked_at")
    evidence_id = _parse_uuid(payload.get("evidence_id"))
    if (
        not isinstance(method, str)
        or not isinstance(result, str)
        or result not in {"verified", "uncertain", "ineligible"}
        or not isinstance(checked_at, str)
        or evidence_id is not None
    ):
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
    facility_id = _parse_uuid(request.args.get("facility_id"))
    if facility_id is None:
        return _error("validation_failed", "A valid facility context is required.", 422)
    if not facility_scope_allows(context, facility_id, "eligibility.record"):
        return _deny_patient_access(context, "eligibility.record", "facility_scope")
    membership = _active_membership(context, facility_id)
    if membership is None:
        return _deny_patient_access(context, "eligibility.record", "facility_scope")
    idempotency_key = _validate_idempotency_key()
    if idempotency_key is None:
        return _error("validation_failed", "A valid Idempotency-Key is required.", 422)
    request_hash = hashlib.sha256(
        json.dumps(
            {
                "patient_id": str(patient_id),
                "facility_id": str(facility_id),
                "method": method,
                "result": result,
                "checked_at": checked_at,
                "evidence_id": str(evidence_id) if evidence_id else None,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).digest()
    establish_patient_context(context, facility_id)
    profile = db.session.scalar(
        _assigned_profiles(context, facility_id).where(
            FacilityPatientProfile.patient_id == patient_id
        )
    )
    if profile is None:
        return _deny_patient_access(context, "eligibility.record", "unavailable")
    # Idempotency: bind to patient + actor + endpoint + request fingerprint
    existing = db.session.scalar(
        select(AdultEligibilityCheck).where(
            AdultEligibilityCheck.patient_id == patient_id,
            AdultEligibilityCheck.facility_id == facility_id,
            AdultEligibilityCheck.idempotency_key == idempotency_key,
            AdultEligibilityCheck.actor_id == context.user.id,
        )
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            return _error(
                "conflict", "The idempotency key was already used with a different request.", 409
            )
        return jsonify(data=_eligibility_projection(existing)), 201
    try:
        # Use transaction-scoped advisory lock for concurrency control
        # (automatically released at transaction end - no lock leakage)
        lock_id = _advisory_lock_key("eligibility", patient_id, facility_id)
        db.session.execute(
            sa.text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": lock_id},
        )
        max_sequence = db.session.scalar(
            select(sa.func.max(AdultEligibilityCheck.sequence)).where(
                AdultEligibilityCheck.patient_id == patient_id,
                AdultEligibilityCheck.facility_id == facility_id,
            )
        )
        next_sequence = (max_sequence or 0) + 1
        now = datetime.now(UTC)
        check = AdultEligibilityCheck(
            id=uuid4(),
            patient_id=patient_id,
            facility_id=facility_id,
            method=method,
            result=result,
            checked_at=now,
            checked_by=context.user.id,
            actor_id=context.user.id,
            evidence_id=evidence_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            sequence=next_sequence,
        )
        db.session.add(check)
        _record_patient_audit(
            context=context,
            permission="eligibility.record",
            action="eligibility.record",
            facility_id=facility_id,
            patient_id=patient_id,
            metadata={"result": result, "method": method[:64]},
        )
        db.session.flush()
        response_data = _eligibility_projection(check)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _error("conflict", "The eligibility check already exists.", 409)
    except SQLAlchemyError as error:
        db.session.rollback()
        if current_app.testing:
            print("DEBUG eligibility error:", error)
        return _service_unavailable()
    except Exception:
        db.session.rollback()
        if current_app.testing:
            import traceback

            traceback.print_exc()
        return _service_unavailable()
    return jsonify(data=response_data), 201


@blueprint.get("/consent-notices")
@require_permission("consent.read")
def list_consent_notices() -> tuple[Response, int]:
    context = get_access_context()
    facility_id = _parse_uuid(request.args.get("facility_id"))
    purpose = request.args.get("purpose")
    language = request.args.get("language")
    if facility_id is None:
        return _error("validation_failed", "A valid facility context is required.", 422)
    if not facility_scope_allows(context, facility_id, "consent.read"):
        return _deny_patient_access(context, "consent.read", "facility_scope")
    establish_patient_context(context, facility_id)
    query = select(ConsentNoticeVersion).where(ConsentNoticeVersion.status == "approved")
    if purpose:
        query = query.where(ConsentNoticeVersion.purpose_codes.contains([purpose]))
    if language is not None:
        query = query.where(ConsentNoticeVersion.language == language)
    notices = list(
        db.session.scalars(
            query.order_by(ConsentNoticeVersion.notice_code, ConsentNoticeVersion.revision)
        ).all()
    )
    data = [
        {
            "id": str(notice.id),
            "notice_code": notice.notice_code,
            "revision": notice.revision,
            "language": notice.language,
            "status": notice.status,
            "purpose_codes": notice.purpose_codes,
            "effective_from": notice.effective_from.isoformat() if notice.effective_from else None,
            "effective_to": notice.effective_to.isoformat() if notice.effective_to else None,
            "version": notice.version,
        }
        for notice in notices
    ]
    try:
        _record_patient_audit(
            context=context,
            permission="consent.read",
            action="consent.notice.list",
            facility_id=facility_id,
            patient_id=None,
            metadata={"purpose": purpose, "language": language},
        )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return _service_unavailable()
    return jsonify(data=data, page={"next_cursor": None}), 200


def _consent_event_projection(event: ConsentEvent) -> dict[str, object]:
    return {
        "id": str(event.id),
        "patient_id": str(event.patient_id),
        "facility_id": str(event.facility_id),
        "episode_id": str(event.episode_id) if event.episode_id else None,
        "notice_version_id": str(event.notice_version_id) if event.notice_version_id else None,
        "purpose": event.purpose,
        "decision": event.decision,
        "method": event.method,
        "language": event.language,
        "effective_at": event.effective_at.isoformat(),
        "recorded_by": str(event.recorded_by),
        "evidence_id": str(event.evidence_id) if event.evidence_id else None,
        "supersedes_id": str(event.supersedes_id) if event.supersedes_id else None,
        "event_sequence": event.event_sequence,
        "version": event.version,
    }


@blueprint.get("/<uuid:patient_id>/consent-events")
@require_permission("consent.read")
def list_consent_events(patient_id: UUID) -> tuple[Response, int]:
    context = get_access_context()
    facility_id = _parse_uuid(request.args.get("facility_id"))
    purpose = request.args.get("purpose")
    if facility_id is None:
        return _error("validation_failed", "A valid facility context is required.", 422)
    if not facility_scope_allows(context, facility_id, "consent.read"):
        return _deny_patient_access(context, "consent.read", "facility_scope")
    membership = _active_membership(context, facility_id)
    if membership is None:
        return _deny_patient_access(context, "consent.read", "facility_scope")
    establish_patient_context(context, facility_id)
    profile = db.session.scalar(
        _assigned_profiles(context, facility_id).where(
            FacilityPatientProfile.patient_id == patient_id
        )
    )
    if profile is None:
        return _deny_patient_access(context, "consent.read", "unavailable")
    query = select(ConsentEvent).where(
        ConsentEvent.patient_id == patient_id, ConsentEvent.facility_id == facility_id
    )
    if purpose:
        query = query.where(ConsentEvent.purpose == purpose)
    events = list(db.session.scalars(query.order_by(ConsentEvent.event_sequence.desc())).all())
    data = [_consent_event_projection(event) for event in events]
    try:
        _record_patient_audit(
            context=context,
            permission="consent.read",
            action="consent.event.list",
            facility_id=facility_id,
            patient_id=patient_id,
            metadata={"purpose": purpose},
        )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return _service_unavailable()
    return jsonify(data=data, page={"next_cursor": None}), 200


@blueprint.post("/<uuid:patient_id>/consent-events")
@require_permission("consent.record")
def record_consent_event(patient_id: UUID) -> tuple[Response, int]:
    context = get_access_context()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error("validation_failed", "A JSON request body is required.", 422)
    if set(payload) - {
        "episode_id",
        "purpose",
        "decision",
        "notice_version_id",
        "method",
        "language",
        "evidence_id",
        "supersedes_id",
    }:
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
    facility_id = _parse_uuid(request.args.get("facility_id"))
    purpose = payload.get("purpose")
    decision = payload.get("decision")
    notice_version_id = _parse_uuid(payload.get("notice_version_id"))
    method = payload.get("method")
    language = payload.get("language")
    evidence_id = _parse_uuid(payload.get("evidence_id"))
    supersedes_id = _parse_uuid(payload.get("supersedes_id"))
    idempotency_key = _validate_idempotency_key()
    if (
        facility_id is None
        or not isinstance(purpose, str)
        or not isinstance(decision, str)
        or decision not in {"grant", "withdraw", "correct"}
        or not isinstance(method, str)
        or not isinstance(language, str)
        or language not in {"en", "lg"}
        or idempotency_key is None
    ):
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
    if not facility_scope_allows(context, facility_id, "consent.record"):
        return _deny_patient_access(context, "consent.record", "facility_scope")
    membership = _active_membership(context, facility_id)
    if membership is None:
        return _deny_patient_access(context, "consent.record", "facility_scope")
    # For withdraw decisions, require the withdraw permission AND facility-scoped withdraw
    if decision == "withdraw":
        if "consent.withdraw" not in context.permissions:
            return _deny_patient_access(context, "consent.withdraw", "permission")
        if not facility_scope_allows(context, facility_id, "consent.withdraw"):
            return _deny_patient_access(context, "consent.withdraw", "facility_scope")
    # Establish RLS context before any data access (notice/evidence validation)
    establish_patient_context(context, facility_id)
    profile = db.session.scalar(
        _assigned_profiles(context, facility_id).where(
            FacilityPatientProfile.patient_id == patient_id
        )
    )
    if profile is None:
        return _deny_patient_access(context, "consent.record", "unavailable")
    # For grant decisions, require a valid approved notice covering the purpose,
    # matching language, and within its effective window; plus valid evidence
    notice = None
    if decision == "grant":
        if notice_version_id is None:
            return _error(
                "validation_failed",
                "A valid approved consent notice is required for grant.",
                422,
            )
        notice = db.session.scalar(
            select(ConsentNoticeVersion).where(
                ConsentNoticeVersion.id == notice_version_id,
                ConsentNoticeVersion.status == "approved",
            )
        )
        if notice is None:
            return _error(
                "validation_failed",
                "A valid approved consent notice is required for grant.",
                422,
            )
        if purpose not in notice.purpose_codes:
            return _error(
                "validation_failed",
                "The notice does not cover the requested purpose.",
                422,
            )
        if notice.language != language:
            return _error(
                "validation_failed",
                "The notice language does not match the request language.",
                422,
            )
        now = datetime.now(UTC)
        if notice.effective_from is not None and notice.effective_from > now:
            return _error(
                "validation_failed",
                "The consent notice is not yet effective.",
                422,
            )
        if notice.effective_to is not None and notice.effective_to <= now:
            return _error(
                "validation_failed",
                "The consent notice has expired.",
                422,
            )
        if evidence_id is None:
            return _error(
                "validation_failed",
                "A valid evidence reference belonging to this facility is required for grant.",
                422,
            )
        evidence = db.session.scalar(
            select(ProtectedEvidence).where(
                ProtectedEvidence.id == evidence_id,
                ProtectedEvidence.facility_id == facility_id,
                ProtectedEvidence.patient_id == patient_id,
            )
        )
        if evidence is None:
            return _error(
                "validation_failed",
                "A valid evidence reference belonging to this patient and "
                "facility is required for grant.",
                422,
            )
        # Enforce patient ownership: evidence must not be referenced by another patient's
        # consent event at this facility
        evidence_used_by_another = db.session.scalar(
            select(ConsentEvent.id).where(
                ConsentEvent.evidence_id == evidence_id,
                ConsentEvent.patient_id != patient_id,
                ConsentEvent.facility_id == facility_id,
            )
        )
        if evidence_used_by_another is not None:
            return _error(
                "validation_failed",
                "This evidence reference is already associated with another patient.",
                422,
            )
    # Validate evidence exists and belongs to the same facility (when provided)
    if evidence_id is not None and decision != "grant":
        evidence = db.session.scalar(
            select(ProtectedEvidence).where(
                ProtectedEvidence.id == evidence_id,
                ProtectedEvidence.facility_id == facility_id,
            )
        )
        if evidence is None:
            return _error(
                "validation_failed",
                "A valid evidence reference belonging to this facility is required.",
                422,
            )
    request_hash = hashlib.sha256(
        json.dumps(
            {
                "patient_id": str(patient_id),
                "facility_id": str(facility_id),
                "purpose": purpose,
                "decision": decision,
                "notice_version_id": str(notice_version_id) if notice_version_id else None,
                "method": method,
                "language": language,
                "evidence_id": str(evidence_id) if evidence_id else None,
                "supersedes_id": str(supersedes_id) if supersedes_id else None,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).digest()
    # Idempotency: bind to patient + recorded_by (actor) + facility + request fingerprint
    existing = db.session.scalar(
        select(ConsentEvent).where(
            ConsentEvent.patient_id == patient_id,
            ConsentEvent.facility_id == facility_id,
            ConsentEvent.idempotency_key == idempotency_key,
            ConsentEvent.recorded_by == context.user.id,
        )
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            return _error(
                "conflict", "The idempotency key was already used with a different request.", 409
            )
        return jsonify(data=_consent_event_projection(existing)), 201
    now = datetime.now(UTC)
    try:
        # Use transaction-scoped advisory lock for concurrency control
        # (automatically released at transaction end - no lock leakage)
        lock_id = _advisory_lock_key("consent", patient_id, facility_id)
        db.session.execute(
            sa.text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": lock_id},
        )
        max_sequence = db.session.scalar(
            select(sa.func.max(ConsentEvent.event_sequence)).where(
                ConsentEvent.patient_id == patient_id,
                ConsentEvent.facility_id == facility_id,
            )
        )
        next_sequence = (max_sequence or 0) + 1
        event = ConsentEvent(
            id=uuid4(),
            patient_id=patient_id,
            facility_id=facility_id,
            episode_id=None,
            notice_version_id=notice_version_id,
            purpose=purpose,
            decision=decision,
            method=method,
            language=language,
            effective_at=now,
            recorded_by=context.user.id,
            evidence_id=evidence_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            supersedes_id=supersedes_id,
            event_sequence=next_sequence,
        )
        db.session.add(event)
        _record_patient_audit(
            context=context,
            permission="consent.record",
            action=f"consent.{decision}",
            facility_id=facility_id,
            patient_id=patient_id,
            metadata={"purpose": purpose, "decision": decision, "method": method[:64]},
        )
        db.session.flush()
        response_data = _consent_event_projection(event)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _error("conflict", "The consent event could not be recorded.", 409)
    except SQLAlchemyError:
        db.session.rollback()
        return _service_unavailable()
    return jsonify(data=response_data), 201


@blueprint.get("/<uuid:patient_id>/evidence/<uuid:evidence_id>")
@require_permission("consent.read")
def get_evidence(patient_id: UUID, evidence_id: UUID) -> tuple[Response, int]:
    context = get_access_context()
    facility_id = _parse_uuid(request.args.get("facility_id"))
    if facility_id is None:
        return _error("validation_failed", "A valid facility context is required.", 422)
    if not facility_scope_allows(context, facility_id, "consent.read"):
        return _deny_patient_access(context, "consent.read", "facility_scope")
    membership = _active_membership(context, facility_id)
    if membership is None:
        return _deny_patient_access(context, "consent.read", "facility_scope")
    establish_patient_context(context, facility_id)
    profile = db.session.scalar(
        _assigned_profiles(context, facility_id).where(
            FacilityPatientProfile.patient_id == patient_id
        )
    )
    if profile is None:
        return _deny_patient_access(context, "consent.read", "unavailable")
    # Evidence must belong to the same patient AND facility
    evidence = db.session.scalar(
        select(ProtectedEvidence).where(
            ProtectedEvidence.id == evidence_id,
            ProtectedEvidence.patient_id == patient_id,
            ProtectedEvidence.facility_id == facility_id,
        )
    )
    if evidence is None:
        return _deny_patient_access(context, "consent.read", "unavailable")
    try:
        _record_patient_audit(
            context=context,
            permission="consent.read",
            action="evidence.access",
            facility_id=facility_id,
            patient_id=patient_id,
            metadata={"evidence_id": str(evidence_id)},
        )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return _service_unavailable()
    return jsonify(
        {
            "id": str(evidence.id),
            "media_type": evidence.media_type,
            "recorded_by": str(evidence.recorded_by),
            "version": evidence.version,
        }
    ), 200


@blueprint.get("/<uuid:patient_id>/evidence")
@require_permission("consent.read")
def list_evidence(patient_id: UUID) -> tuple[Response, int]:
    context = get_access_context()
    facility_id = _parse_uuid(request.args.get("facility_id"))
    if facility_id is None:
        return _error("validation_failed", "A valid facility context is required.", 422)
    if not facility_scope_allows(context, facility_id, "consent.read"):
        return _deny_patient_access(context, "consent.read", "facility_scope")
    membership = _active_membership(context, facility_id)
    if membership is None:
        return _deny_patient_access(context, "consent.read", "facility_scope")
    establish_patient_context(context, facility_id)
    profile = db.session.scalar(
        _assigned_profiles(context, facility_id).where(
            FacilityPatientProfile.patient_id == patient_id
        )
    )
    if profile is None:
        return _deny_patient_access(context, "consent.read", "unavailable")
    evidence_rows = list(
        db.session.scalars(
            select(ProtectedEvidence).where(
                ProtectedEvidence.patient_id == patient_id,
                ProtectedEvidence.facility_id == facility_id,
            )
        ).all()
    )
    try:
        _record_patient_audit(
            context=context,
            permission="consent.read",
            action="evidence.list",
            facility_id=facility_id,
            patient_id=patient_id,
        )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return _service_unavailable()
    return jsonify(
        data=[
            {
                "id": str(evidence.id),
                "media_type": evidence.media_type,
                "version": evidence.version,
            }
            for evidence in evidence_rows
        ]
    ), 200


@blueprint.post("/<uuid:patient_id>/evidence")
@require_permission("consent.record")
def register_evidence(patient_id: UUID) -> tuple[Response, int]:
    """Register synthetic evidence for a patient so it can be selected during consent grant."""
    context = get_access_context()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error("validation_failed", "A JSON request body is required.", 422)
    if set(payload) - {"storage_reference", "digest", "media_type"}:
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
    facility_id = _parse_uuid(request.args.get("facility_id"))
    storage_reference = payload.get("storage_reference")
    digest = payload.get("digest")
    media_type = payload.get("media_type")
    if (
        facility_id is None
        or not isinstance(storage_reference, str)
        or not isinstance(digest, str)
        or not isinstance(media_type, str)
        or media_type not in {"application/pdf", "image/png", "image/jpeg"}
    ):
        return _error("validation_failed", UNKNOWN_FIELD_MESSAGE, 422)
    # Reject path traversal in storage reference
    if ".." in storage_reference or storage_reference.startswith("/") or "\\" in storage_reference:
        return _error(
            "validation_failed",
            "Storage reference contains invalid characters.",
            422,
        )
    # Validate digest is a hex-encoded SHA-256 (64 hex chars = 32 bytes)
    try:
        digest_bytes = bytes.fromhex(digest)
    except ValueError:
        return _error(
            "validation_failed",
            "Digest must be a hex-encoded SHA-256 hash.",
            422,
        )
    if len(digest_bytes) != 32:
        return _error(
            "validation_failed",
            "Digest must be a SHA-256 hash (32 bytes).",
            422,
        )
    if not facility_scope_allows(context, facility_id, "consent.record"):
        return _deny_patient_access(context, "consent.record", "facility_scope")
    membership = _active_membership(context, facility_id)
    if membership is None:
        return _deny_patient_access(context, "consent.record", "facility_scope")
    establish_patient_context(context, facility_id)
    profile = db.session.scalar(
        _assigned_profiles(context, facility_id).where(
            FacilityPatientProfile.patient_id == patient_id
        )
    )
    if profile is None:
        return _deny_patient_access(context, "consent.record", "unavailable")
    idempotency_key = _validate_idempotency_key()
    if idempotency_key is None:
        return _error("validation_failed", "A valid Idempotency-Key is required.", 422)
    # Idempotency: check if evidence with same storage reference already exists
    existing = db.session.scalar(
        select(ProtectedEvidence).where(
            ProtectedEvidence.patient_id == patient_id,
            ProtectedEvidence.facility_id == facility_id,
            ProtectedEvidence.storage_reference == storage_reference,
        )
    )
    if existing is not None:
        return jsonify(
            data={
                "id": str(existing.id),
                "media_type": existing.media_type,
                "version": existing.version,
            }
        ), 201
    try:
        evidence = ProtectedEvidence(
            id=uuid4(),
            patient_id=patient_id,
            facility_id=facility_id,
            storage_reference=storage_reference,
            digest=bytes.fromhex(digest),
            media_type=media_type,
            recorded_by=context.user.id,
        )
        db.session.add(evidence)
        _record_patient_audit(
            context=context,
            permission="consent.record",
            action="evidence.register",
            facility_id=facility_id,
            patient_id=patient_id,
            metadata={"storage_reference": storage_reference},
        )
        db.session.flush()
        response_data = {
            "id": str(evidence.id),
            "media_type": evidence.media_type,
            "version": evidence.version,
        }
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _error("conflict", "The evidence reference already exists.", 409)
    except SQLAlchemyError:
        db.session.rollback()
        return _service_unavailable()
    return jsonify(data=response_data), 201
