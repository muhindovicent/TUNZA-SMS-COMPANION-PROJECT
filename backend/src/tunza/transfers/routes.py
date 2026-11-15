"""Controlled fixture transfers, never approved live transfer authority."""

import hashlib
import json
import re
from uuid import UUID, uuid4

import sqlalchemy as sa
from flask import current_app, g, jsonify, request
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from tunza.access import get_access_context, require_authenticated, require_permission
from tunza.audit import record_audit
from tunza.extensions import db
from tunza.identity.models import Facility
from tunza.patients.models import EpisodeFacilityAssignment, PregnancyEpisode
from tunza.patients.routes import _advisory_lock_key, _parse_uuid, _validate_idempotency_key
from tunza.patients.security import establish_patient_context
from tunza.pregnancy.routes import _scope
from tunza.services.encryption import decrypt_json, encrypt_json
from tunza.transfers import blueprint
from tunza.transfers.models import (
    FacilityTransfer,
    TransferDecision,
    TransferHandover,
    TransferIdempotency,
)

CODE = re.compile(r"^SYN-[A-Z0-9][A-Z0-9-]{1,59}$")


def _error(code, message, status):
    return jsonify(error={"code": code, "message": message, "request_id": g.request_id}), status


def _enabled():
    return (
        current_app.config["APP_ENV"] in {"testing", "development"}
        and current_app.config["SYNTHETIC_TRANSFERS_ENABLED"]
    )


def _audit(context, action, facility=None, resource=None, outcome="success"):
    record_audit(
        permission="transfer",
        action=action,
        resource_type="transfer",
        outcome=outcome,
        actor_id=context.user.id,
        facility_id=facility,
        resource_id=resource,
        request_id=g.request_id,
    )


def _denied(context, policy=False):
    try:
        _audit(context, "transfer.policy" if policy else "transfer.access", outcome="denied")
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return _error("service_unavailable", "The request could not be completed.", 503)
    return _error(
        "policy_unconfigured" if policy else "not_found",
        "Synthetic transfers are disabled; D04/D09 remain unresolved."
        if policy
        else "The request could not be completed.",
        403 if policy else 404,
    )


def _context(context, facility, permissions):
    if not _enabled():
        return _denied(context, policy=True)
    if facility is None:
        return _error("validation_failed", "A valid facility context is required.", 422)
    if not any(_scope(context, facility, permission) for permission in permissions):
        return _denied(context)
    establish_patient_context(context, facility)
    db.session.execute(sa.text("SELECT set_config('tunza.synthetic_transfers','on',true)"))
    return None


def _crypt(data, purpose):
    return encrypt_json(
        data,
        key=current_app.config["PATIENT_ENCRYPTION_KEY"],
        key_version=current_app.config["PATIENT_KEY_VERSION"],
        purpose=purpose,
    )


def _decrypt(data, version, purpose):
    return decrypt_json(
        data, key=current_app.config["PATIENT_ENCRYPTION_KEY"], key_version=version, purpose=purpose
    )


def _projection(row, facility):
    # Source receipts never contain destination reference/receiver/assignment.
    decision = db.session.scalar(
        select(TransferDecision).where(TransferDecision.transfer_id == row.id)
    )
    result = {
        "id": str(row.id),
        "source_facility_id": str(row.source_facility_id),
        "destination_facility_id": str(row.destination_facility_id),
        "state": row.state,
        "version": row.version,
        "synthetic": True,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }
    if facility == row.source_facility_id:
        result["source_assignment_id"] = str(row.source_assignment_id)
    if decision:
        result["decision"] = {
            "id": str(decision.id),
            "outcome": decision.outcome,
            "reason_code": decision.reason_code,
            "recorded_at": decision.created_at.isoformat(),
        }
        if facility == row.destination_facility_id and decision.outcome == "accepted":
            result["decision"].update(
                destination_assignment_id=str(decision.destination_assignment_id),
                receiver_membership_id=str(decision.receiver_membership_id),
                destination_reference=decision.destination_reference,
            )
    return result


def _response(data, status=200):
    response = jsonify(data=data)
    if isinstance(data, dict) and "version" in data:
        response.headers["ETag"] = f'"{data["version"]}"'
    return response, status


def _lock(context, facility, operation, key, body, target=None):
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "actor": str(context.user.id),
                "facility": str(facility),
                "operation": operation,
                "target": str(target) if target else None,
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
    previous = db.session.scalar(
        select(TransferIdempotency).where(
            TransferIdempotency.actor_id == context.user.id,
            TransferIdempotency.operation == operation,
            TransferIdempotency.key == key,
        )
    )
    if previous:
        if previous.request_hash != fingerprint:
            db.session.rollback()
            return fingerprint, _error("conflict", "The idempotency key was already used.", 409)
        data = _decrypt(previous.response_cipher, previous.key_version, "transfer-response")
        status = previous.response_status
        _audit(context, "transfer.replay", facility, previous.transfer_id)
        db.session.commit()
        return fingerprint, _response(data, status)
    return fingerprint, None


def _save(context, facility, operation, key, fingerprint, row, status):
    data = _projection(row, facility)
    db.session.add(
        TransferIdempotency(
            id=uuid4(),
            actor_id=context.user.id,
            facility_id=facility,
            transfer_id=row.id,
            operation=operation,
            key=key,
            request_hash=fingerprint,
            response_cipher=_crypt(data, "transfer-response"),
            response_status=status,
            key_version=current_app.config["PATIENT_KEY_VERSION"],
        )
    )
    _audit(context, operation, facility, row.id)
    db.session.commit()
    return _response(data, status)


def _failure(context, error):
    db.session.rollback()
    state = getattr(getattr(error, "orig", None), "sqlstate", None)
    if state == "42501":
        return _denied(context)
    status = {"TZ412": 412, "TZ409": 409, "TZ422": 422, "23505": 409, "23P01": 409}.get(state, 503)
    return _error(
        {412: "precondition_failed", 409: "conflict", 422: "validation_failed"}.get(
            status, "service_unavailable"
        ),
        "The transfer could not be completed. Refresh its current state.",
        status,
    )


@blueprint.get("/capabilities")
@require_authenticated
def capabilities():
    return jsonify(
        data={
            "synthetic_enabled": bool(_enabled()),
            "live_enabled": False,
            "unresolved_decisions": ["D04", "D09"],
        }
    )


@blueprint.get("/destinations")
@require_permission("transfer.initiate")
def destinations():
    context = get_access_context()
    facility = _parse_uuid(request.args.get("facility_id"))
    try:
        denied = _context(context, facility, ["transfer.initiate"])
        if denied:
            return denied
        rows = db.session.scalars(
            select(Facility)
            .where(
                Facility.active.is_(True),
                Facility.id != facility,
                Facility.code.op("~")(r"^(SYN|TST)[A-Z0-9-]*$"),
            )
            .order_by(Facility.code)
        ).all()
        data = [{"id": str(row.id), "code": row.code, "name": row.name} for row in rows]
        _audit(context, "transfer.destinations", facility)
        db.session.commit()
        return _response(data)
    except SQLAlchemyError as error:
        return _failure(context, error)


@blueprint.post("")
@require_permission("transfer.initiate")
def initiate():
    context = get_access_context()
    facility = _parse_uuid(request.args.get("facility_id"))
    try:
        denied = _context(context, facility, ["transfer.initiate"])
        if denied:
            return denied
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) - {
            "source_assignment_id",
            "destination_facility_id",
            "handover",
            "agreement_evidence_id",
        }:
            return _error("validation_failed", "Request validation failed.", 422)
        source_id = _parse_uuid(payload.get("source_assignment_id"))
        destination = _parse_uuid(payload.get("destination_facility_id"))
        handover = payload.get("handover")
        if (
            source_id is None
            or destination is None
            or destination == facility
            or payload.get("agreement_evidence_id") is not None
            or not isinstance(handover, dict)
            or set(handover) != {"synthetic", "fixture_code"}
            or handover["synthetic"] is not True
            or not isinstance(handover["fixture_code"], str)
            or not CODE.fullmatch(handover["fixture_code"])
        ):
            return _error(
                "validation_failed",
                "Only minimal synthetic fixture metadata is supported; D09 is unresolved.",
                422,
            )
        key = _validate_idempotency_key()
        if key is None:
            return _error("validation_failed", "A valid Idempotency-Key is required.", 422)
        body = {
            "source_assignment_id": str(source_id),
            "destination_facility_id": str(destination),
            "handover": handover,
        }
        fingerprint, replay = _lock(context, facility, "transfer.initiate", key, body)
        if replay:
            return replay
        source = db.session.scalar(
            select(EpisodeFacilityAssignment)
            .join(PregnancyEpisode, PregnancyEpisode.id == EpisodeFacilityAssignment.episode_id)
            .where(
                EpisodeFacilityAssignment.id == source_id,
                EpisodeFacilityAssignment.facility_id == facility,
            )
        )
        if source is None:
            return _denied(context)
        episode = db.session.scalar(
            select(PregnancyEpisode)
            .where(PregnancyEpisode.id == source.episode_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if episode is None:
            return _denied(context)
        if request.headers.get("If-Match") is None:
            return _error("precondition_required", "A pregnancy If-Match version is required.", 428)
        if request.headers["If-Match"] != f'"{episode.version}"':
            return _error("precondition_failed", "The pregnancy changed. Refresh it.", 412)
        facilities = db.session.scalars(
            select(Facility).where(
                Facility.id.in_([facility, destination]),
                Facility.active.is_(True),
                Facility.code.op("~")(r"^(SYN|TST)[A-Z0-9-]*$"),
            )
        ).all()
        if len(facilities) != 2 or episode.lifecycle != "ongoing" or source.valid_to is not None:
            return _error(
                "conflict",
                "A current synthetic source and distinct active synthetic destination "
                "are required.",
                409,
            )
        row = FacilityTransfer(
            id=uuid4(),
            source_assignment_id=source.id,
            episode_id=episode.id,
            patient_id=episode.patient_id,
            source_facility_id=facility,
            destination_facility_id=destination,
            requested_by=context.user.id,
        )
        db.session.add(row)
        db.session.flush()
        db.session.add(
            TransferHandover(
                id=uuid4(),
                transfer_id=row.id,
                prepared_by=context.user.id,
                handover_cipher=_crypt(handover, "synthetic-transfer-handover"),
                key_version=current_app.config["PATIENT_KEY_VERSION"],
            )
        )
        episode.version += 1
        db.session.flush()
        return _save(context, facility, "transfer.initiate", key, fingerprint, row, 201)
    except SQLAlchemyError as error:
        return _failure(context, error)


def _decide(transfer_id, cancel=False):
    context = get_access_context()
    facility = _parse_uuid(request.args.get("facility_id"))
    permission = "transfer.cancel" if cancel else "transfer.accept"
    operation = "transfer.cancel" if cancel else "transfer.decision"
    try:
        denied = _context(context, facility, [permission])
        if denied:
            return denied
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) - (
            {"reason"}
            if cancel
            else {"decision", "reason", "receiver_membership_id", "synthetic_language"}
        ):
            return _error("validation_failed", "Request validation failed.", 422)
        decision = "cancel" if cancel else payload.get("decision")
        reason = payload.get("reason")
        receiver = _parse_uuid(payload.get("receiver_membership_id"))
        language = payload.get("synthetic_language")
        if (
            not isinstance(decision, str)
            or decision not in {"accept", "reject", "cancel"}
            or (not cancel and decision == "cancel")
            or not isinstance(reason, str)
            or not CODE.fullmatch(reason)
            or (
                decision == "accept"
                and (
                    receiver is None
                    or not isinstance(language, str)
                    or language not in {"en", "lg"}
                )
            )
            or (
                decision != "accept"
                and ("receiver_membership_id" in payload or "synthetic_language" in payload)
            )
        ):
            return _error(
                "validation_failed",
                "A synthetic decision code and explicit destination setup are required.",
                422,
            )
        key = _validate_idempotency_key()
        if key is None:
            return _error("validation_failed", "A valid Idempotency-Key is required.", 422)
        fingerprint, replay = _lock(context, facility, operation, key, payload, transfer_id)
        if replay:
            return replay
        row = db.session.get(FacilityTransfer, transfer_id)
        if row is None:
            return _denied(context)
        match = request.headers.get("If-Match")
        if match is None:
            return _error("precondition_required", "A transfer If-Match version is required.", 428)
        if not re.fullmatch(r'"[1-9][0-9]{0,8}"', match):
            return _error("precondition_failed", "A current transfer version is required.", 412)
        outcome = {"accept": "accepted", "reject": "rejected", "cancel": "cancelled"}[decision]
        db.session.execute(
            sa.text(
                "SELECT tunza_synthetic_transfer_decide(:id,:version,:outcome,"
                ":reason,:receiver,:language,:cipher,:key_version)"
            ),
            {
                "id": transfer_id,
                "version": int(match[1:-1]),
                "outcome": outcome,
                "reason": reason,
                "receiver": receiver,
                "language": language,
                "cipher": _crypt({}, "patient-demographics"),
                "key_version": current_app.config["PATIENT_KEY_VERSION"],
            },
        )
        row = db.session.scalar(
            select(FacilityTransfer)
            .where(FacilityTransfer.id == transfer_id)
            .execution_options(populate_existing=True)
        )
        return _save(context, facility, operation, key, fingerprint, row, 200)
    except SQLAlchemyError as error:
        return _failure(context, error)


@blueprint.post("/<uuid:transfer_id>/decision")
@require_permission("transfer.accept")
def decide(transfer_id):
    return _decide(transfer_id)


@blueprint.post("/<uuid:transfer_id>/cancel")
@require_permission("transfer.cancel")
def cancel(transfer_id):
    return _decide(transfer_id, cancel=True)


@blueprint.get("/<uuid:transfer_id>")
@require_authenticated
def detail(transfer_id):
    return _read(transfer_id)


@blueprint.get("/<uuid:transfer_id>/handover")
@require_authenticated
def handover(transfer_id):
    return _read(transfer_id, handover=True)


def _read(transfer_id, handover=False):
    context = get_access_context()
    facility = _parse_uuid(request.args.get("facility_id"))
    try:
        denied = _context(
            context, facility, ["transfer.initiate", "transfer.accept", "transfer.cancel"]
        )
        if denied:
            return denied
        row = db.session.get(FacilityTransfer, transfer_id)
        if row is None:
            return _denied(context)
        if handover:
            record = db.session.scalar(
                select(TransferHandover).where(TransferHandover.transfer_id == row.id)
            )
            if record is None:
                return _denied(context)
            data = _decrypt(
                record.handover_cipher, record.key_version, "synthetic-transfer-handover"
            )
        else:
            data = _projection(row, facility)
        _audit(context, "transfer.handover" if handover else "transfer.read", facility, row.id)
        db.session.commit()
        return _response(data)
    except SQLAlchemyError as error:
        return _failure(context, error)


@blueprint.get("/<uuid:transfer_id>/receivers")
@require_permission("transfer.accept")
def receivers(transfer_id):
    context = get_access_context()
    facility = _parse_uuid(request.args.get("facility_id"))
    try:
        denied = _context(context, facility, ["transfer.accept"])
        if denied:
            return denied
        row = db.session.get(FacilityTransfer, transfer_id)
        if row is None or facility != row.destination_facility_id or row.state != "pending":
            return _denied(context)
        rows = db.session.execute(
            sa.text("""
            SELECT m.id, u.display_name FROM facility_memberships m JOIN users u ON u.id = m.user_id
            WHERE m.facility_id = :facility AND m.status = 'active' AND u.status = 'active'
              AND m.starts_at <= clock_timestamp()
              AND (m.ends_at IS NULL OR m.ends_at > clock_timestamp())
              AND EXISTS (SELECT 1 FROM role_bindings b JOIN roles r ON r.id = b.role_id
                WHERE b.user_id = m.user_id AND b.facility_id = :facility
                AND b.scope_kind = r.scope_kind
                AND r.code = 'HCW' AND r.scope_kind = 'facility'
                AND NOT EXISTS (
                    SELECT 1 FROM (VALUES ('patient.read'),('pregnancy.read')) required(code)
                    WHERE NOT EXISTS (SELECT 1 FROM role_permissions rp
                        JOIN permissions perm ON perm.id = rp.permission_id
                        WHERE rp.role_id = r.id AND perm.code = required.code))
                AND b.starts_at <= clock_timestamp()
                AND (b.ends_at IS NULL OR b.ends_at > clock_timestamp())) ORDER BY m.id
        """),
            {"facility": facility},
        ).all()
        data = [{"id": str(record.id), "display_name": record.display_name} for record in rows]
        _audit(context, "transfer.receivers", facility, row.id)
        db.session.commit()
        return _response(data)
    except SQLAlchemyError as error:
        return _failure(context, error)


@blueprint.get("")
@require_authenticated
def list_transfers():
    context = get_access_context()
    facility = _parse_uuid(request.args.get("facility_id"))
    direction = request.args.get("direction", "incoming")
    try:
        denied = _context(
            context,
            facility,
            ["transfer.accept"]
            if direction == "incoming"
            else ["transfer.initiate", "transfer.cancel"],
        )
        if denied:
            return denied
        try:
            state = request.args.get("state", "pending")
            episode = request.args.get("episode_id")
            limit = int(request.args.get("limit", "25"))
            if (
                direction not in {"incoming", "outgoing"}
                or state not in {"all", "pending", "accepted", "rejected", "cancelled"}
                or not 1 <= limit <= 100
            ):
                raise ValueError()
            query = select(FacilityTransfer).where(
                (
                    FacilityTransfer.destination_facility_id
                    if direction == "incoming"
                    else FacilityTransfer.source_facility_id
                )
                == facility
            )
            if state != "all":
                query = query.where(FacilityTransfer.state == state)
            if episode:
                query = query.where(FacilityTransfer.episode_id == UUID(episode))
            binding = [str(context.user.id), str(facility), direction, state, episode]
            serializer = URLSafeSerializer(current_app.config["SECRET_KEY"], salt="transfer-cursor")
            cursor = request.args.get("cursor")
            if cursor:
                decoded = serializer.loads(cursor)
                if decoded["scope"] != binding:
                    raise ValueError()
                from datetime import datetime

                query = query.where(
                    sa.tuple_(FacilityTransfer.created_at, FacilityTransfer.id)
                    > sa.tuple_(datetime.fromisoformat(decoded["time"]), UUID(decoded["id"]))
                )
        except (ValueError, TypeError, KeyError, BadSignature):
            return _error("validation_failed", "Invalid transfer filters or cursor.", 422)
        rows = db.session.scalars(
            query.order_by(FacilityTransfer.created_at, FacilityTransfer.id).limit(limit + 1)
        ).all()
        data = [_projection(row, facility) for row in rows[:limit]]
        next_cursor = (
            serializer.dumps(
                {
                    "scope": binding,
                    "time": rows[limit - 1].created_at.isoformat(),
                    "id": str(rows[limit - 1].id),
                }
            )
            if len(rows) > limit
            else None
        )
        _audit(context, "transfer.list", facility)
        db.session.commit()
        return jsonify(data=data, page={"next_cursor": next_cursor})
    except SQLAlchemyError as error:
        return _failure(context, error)
