"""Scoped held-work planning, read-only operations projections and synthetic controls."""

import hashlib
import json
import re
from decimal import Decimal
from uuid import UUID, uuid4

import sqlalchemy as sa
from flask import current_app, g, jsonify, request
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from tunza.access import get_access_context, require_authenticated, require_permission
from tunza.appointments.routes import _iso, _utc
from tunza.audit import record_audit
from tunza.extensions import db
from tunza.messaging import blueprint
from tunza.messaging.models import (
    CostReservation,
    MessageCommand,
    MessageOccurrence,
    MessageOperation,
    MessageOperationEvent,
    MessageSchedulingEvent,
    MessagingBudget,
    MessagingPolicy,
)
from tunza.messaging.segments import estimate_segments
from tunza.messaging.service import (
    Conflict,
    control_lock,
    evaluate,
    occurrence_data,
    plan,
    policy,
    reserve,
)
from tunza.patients.models import PregnancyEpisode
from tunza.patients.routes import _advisory_lock_key, _parse_uuid, _validate_idempotency_key
from tunza.services.encryption import decrypt_json, encrypt_json


def error(code, status):
    return jsonify(
        error={
            "code": code,
            "message": "The messaging request could not be completed.",
            "request_id": g.request_id,
        }
    ), status


def audit(permission, action, resource_type, resource=None, state=None, *, outcome="success"):
    return record_audit(
        permission=permission,
        action=action,
        resource_type=resource_type,
        resource_id=resource,
        actor_id=get_access_context().user.id,
        facility_id=db.session.scalar(select(sa.func.tunza_current_uuid("tunza.facility_id"))),
        outcome=outcome,
        request_id=g.request_id,
        metadata={"state": state} if state else {},
    )


def context(facility, permission):
    current = get_access_context()
    g.messaging_permission = permission
    db.session.execute(
        sa.text(
            "SELECT set_config('tunza.actor_id',:actor,true), "
            "set_config('tunza.facility_id',:facility,true), "
            "set_config('tunza.patient_id','',true)"
        ),
        {"actor": str(current.user.id), "facility": str(facility) if facility else ""},
    )
    if not db.session.scalar(select(sa.func.tunza_message_permission(permission, facility))):
        raise PermissionError()
    return current


def crypt(data):
    return encrypt_json(
        data,
        key=current_app.config["PATIENT_ENCRYPTION_KEY"],
        key_version=current_app.config["PATIENT_KEY_VERSION"],
        purpose="message-command",
    )


def normalize_plan(body):
    keys = {
        "episode_id",
        "kind",
        "due_at",
        "expires_at",
        "gestational_week",
        "appointment_id",
        "reminder_code",
        "template_id",
    }
    if not isinstance(body, dict) or set(body) - keys:
        raise ValueError()
    episode = _parse_uuid(body["episode_id"])
    if episode is None:
        raise ValueError()
    kind = body["kind"]
    week, appointment, code = (
        body.get("gestational_week"),
        body.get("appointment_id"),
        body.get("reminder_code"),
    )
    if kind == "weekly":
        if (
            not isinstance(week, int)
            or isinstance(week, bool)
            or not 0 <= week <= 2147483647
            or appointment is not None
            or code is not None
        ):
            raise ValueError()
    elif kind == "appointment_reminder":
        appointment = _parse_uuid(appointment)
        if appointment is None:
            raise ValueError()
        if (
            week is not None
            or not isinstance(code, str)
            or not re.fullmatch(r"SYN-[A-Z0-9-]{2,60}", code)
        ):
            raise ValueError()
    else:
        raise ValueError()
    due, end = _utc(body["due_at"]), _utc(body["expires_at"])
    if end <= due:
        raise ValueError()
    template_id = _parse_uuid(body["template_id"]) if body.get("template_id") else None
    if body.get("template_id") and template_id is None:
        raise ValueError()
    return episode, {
        "kind": kind,
        "due_at": due,
        "expires_at": end,
        "gestational_week": week,
        "appointment_id": appointment,
        "reminder_code": code,
        "template_id": template_id,
    }


def command(operation, facility, payload, callback, *, episode_id=None, template_id=None):
    current = get_access_context()
    key = _validate_idempotency_key()
    if key is None:
        return error("validation_failed", 422)
    digest = hashlib.sha256(
        json.dumps(
            {"facility": str(facility), "episode": str(episode_id), "payload": payload},
            sort_keys=True,
            default=str,
        ).encode()
    ).digest()
    db.session.execute(
        sa.text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": _advisory_lock_key("message." + operation + key, current.user.id, current.user.id)},
    )
    control_lock()
    episode = None
    if episode_id:
        episode = db.session.scalar(
            select(PregnancyEpisode)
            .where(PregnancyEpisode.id == episode_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if episode is None or not db.session.scalar(
            select(sa.func.tunza_message_care(episode_id, facility, "message.plan"))
        ):
            raise PermissionError()
        # Same consent lock used by grant/withdraw commands. A committed withdrawal
        # cannot fall between our eligibility snapshot and candidate persistence.
        db.session.execute(
            sa.text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": _advisory_lock_key("consent", episode.patient_id, facility)},
        )
        if template_id:
            # Acquire before occurrence locks: content suspension invalidates those
            # rows while holding the matching Phase 5A template advisory lock.
            db.session.execute(
                sa.text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": _advisory_lock_key("content.template", template_id, template_id)},
            )
    cached = db.session.scalar(
        select(MessageCommand).where(
            MessageCommand.actor_id == current.user.id,
            MessageCommand.operation == operation,
            MessageCommand.key == key,
        )
    )
    if cached:
        if cached.request_hash != digest:
            raise Conflict()
        data = decrypt_json(
            cached.response_cipher,
            key=current_app.config["PATIENT_ENCRYPTION_KEY"],
            key_version=cached.key_version,
            purpose="message-command",
        )
        audit(
            "message.plan" if episode else "messaging.control",
            "messaging.replay",
            "message_command",
        )
        status = cached.response_status
    else:
        data, status = callback(current, episode, key)
        if status >= 400:
            db.session.rollback()
            return error(
                "precondition_required" if status == 428 else "precondition_failed", status
            )
        db.session.add(
            MessageCommand(
                id=uuid4(),
                actor_id=current.user.id,
                facility_id=facility,
                episode_id=episode_id,
                operation=operation,
                key=key,
                request_hash=digest,
                response_cipher=crypt(data),
                key_version=current_app.config["PATIENT_KEY_VERSION"],
                response_status=status,
            )
        )
    db.session.commit()
    response = jsonify(data=data)
    if isinstance(data, dict) and "version" in data:
        response.headers["ETag"] = f'"{data["version"]}"'
    return response, status


def guarded(callback):
    try:
        return callback()
    except PermissionError:
        db.session.rollback()
        try:
            audit(
                getattr(g, "messaging_permission", "message.read"),
                "messaging.denied",
                "endpoint",
                outcome="denied",
            )
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            return error("service_unavailable", 503)
        return error("not_found", 404)
    except (Conflict, IntegrityError):
        db.session.rollback()
        return error("conflict", 409)
    except (ValueError, TypeError, KeyError):
        db.session.rollback()
        return error("validation_failed", 422)
    except SQLAlchemyError:
        db.session.rollback()
        return error("service_unavailable", 503)


@blueprint.post("/message-occurrences")
@require_permission("message.plan")
def plan_occurrence():
    def run():
        facility = UUID(request.args["facility_id"])
        current = context(facility, "message.plan")
        episode_id, body = normalize_plan(request.get_json(silent=True))

        def save(_current, episode, _key):
            now = db.session.scalar(select(sa.func.clock_timestamp()))
            row, status = plan(current, episode, facility, body, now, audit)
            return occurrence_data(row), status

        return command(
            "plan", facility, body, save, episode_id=episode_id, template_id=body["template_id"]
        )

    return guarded(run)


@blueprint.post("/message-occurrences/<uuid:target>/evaluate")
@require_permission("message.plan")
def recheck(target):
    def run():
        facility = UUID(request.args["facility_id"])
        context(facility, "message.plan")
        if request.get_json(silent=True) != {}:
            raise ValueError()
        row = db.session.get(MessageOccurrence, target)
        if row is None:
            raise PermissionError()

        def save(_current, episode, _key):
            row = db.session.scalar(
                select(MessageOccurrence)
                .where(MessageOccurrence.id == target)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if row is None:
                raise PermissionError()
            if request.headers.get("If-Match") is None:
                return {"hold_reason": "precondition_required"}, 428
            if request.headers["If-Match"] != f'"{row.version}"':
                return {"hold_reason": "precondition_failed"}, 412
            evaluate(row, episode, db.session.scalar(select(sa.func.clock_timestamp())), audit)
            return occurrence_data(row), 200

        return command(
            "evaluate",
            facility,
            {"target": str(target)},
            save,
            episode_id=row.episode_id,
            template_id=row.template_id,
        )

    return guarded(run)


def projection(row):
    return {
        "id": str(row.id),
        "facility_id": str(row.facility_id),
        "kind": row.kind,
        "state": row.state,
        "hold_reasons": row.hold_reasons,
        "version": row.version,
        "due_at": _iso(row.due_at),
        "expires_at": _iso(row.expires_at),
        "synthetic_only": True,
        "dispatch_eligible": False,
    }


@blueprint.get("/message-occurrences")
@require_authenticated
def occurrences():
    def run():
        facility = _parse_uuid(request.args.get("facility_id"))
        if request.args.get("facility_id") is not None and facility is None:
            raise ValueError()
        operations = request.args.get("view") == "operations"
        permission = "message.ops" if operations else "message.read"
        context(facility, permission)
        if not operations and facility is None:
            raise ValueError()
        model = MessageOperation if operations else MessageOccurrence
        query = select(model)
        if facility:
            query = query.where(model.facility_id == facility)
        state = request.args.get("state")
        if state:
            if state not in {"held", "suppressed", "cancelled", "expired"}:
                raise ValueError()
            query = query.where(model.state == state)
        limit = int(request.args.get("limit", "25"))
        if not 1 <= limit <= 100:
            raise ValueError()
        signer = URLSafeSerializer(current_app.config["SECRET_KEY"], salt="message-cursor")
        scope = [str(get_access_context().user.id), str(facility), operations, state]
        if request.args.get("cursor"):
            try:
                cursor = signer.loads(request.args["cursor"])
                if cursor["scope"] != scope:
                    raise ValueError()
                query = query.where(
                    sa.tuple_(model.created_at, model.id)
                    > sa.tuple_(_utc(cursor["time"]), UUID(cursor["id"]))
                )
            except BadSignature:
                raise ValueError() from None
        rows = db.session.scalars(query.order_by(model.created_at, model.id).limit(limit + 1)).all()
        data = [projection(row) if operations else occurrence_data(row) for row in rows[:limit]]
        next_cursor = (
            signer.dumps(
                {
                    "scope": scope,
                    "time": _iso(rows[limit - 1].created_at),
                    "id": str(rows[limit - 1].id),
                }
            )
            if len(rows) > limit
            else None
        )
        audit(permission, "messaging.list", "message_occurrence")
        db.session.commit()
        return jsonify(data=data, page={"next_cursor": next_cursor})

    return guarded(run)


@blueprint.get("/message-occurrences/<uuid:target>/events")
@require_authenticated
def history(target):
    def run():
        facility = _parse_uuid(request.args.get("facility_id"))
        if request.args.get("facility_id") is not None and facility is None:
            raise ValueError()
        operations = request.args.get("view") == "operations"
        permission = "message.ops" if operations else "message.read"
        context(facility, permission)
        parent = db.session.get(MessageOperation if operations else MessageOccurrence, target)
        if parent is None or (facility and parent.facility_id != facility):
            raise PermissionError()
        model = MessageOperationEvent if operations else MessageSchedulingEvent
        rows = db.session.scalars(
            select(model).where(model.occurrence_id == target).order_by(model.sequence)
        ).all()
        audit(permission, "messaging.history", "message_occurrence", target)
        result = [
            {
                "sequence": row.sequence,
                "event_type": row.event_type,
                "state": row.state,
                "hold_reasons": row.hold_reasons,
                "created_at": _iso(row.created_at),
            }
            for row in rows
        ]
        db.session.commit()
        return jsonify(data=result)

    return guarded(run)


def scope_id(facility):
    return UUID(facility) if facility else None


@blueprint.route("/messaging/global/pause", methods=["GET", "POST"])
@blueprint.route("/messaging/facilities/<facility>/pause", methods=["GET", "POST"])
@blueprint.route("/messaging/global/policies", methods=["GET", "POST"])
@blueprint.route("/messaging/facilities/<facility>/policies", methods=["GET", "POST"])
@require_permission("messaging.control")
def controls(facility=None):
    def run():
        scope = scope_id(facility)
        context(scope, "messaging.control")

        def data(row):
            return {
                "paused": row.paused if row else True,
                "version": row.revision if row else 0,
                "code": row.code if row else "policy_missing",
                "synthetic_only": True,
                "dispatch_eligible": False,
            }

        if request.method == "GET":
            result = data(policy(scope))
            audit("messaging.control", "messaging.control.read", "messaging_policy")
            db.session.commit()
            response = jsonify(data=result)
            response.headers["ETag"] = f'"{result["version"]}"'
            return response
        body = request.get_json(silent=True)
        if (
            not isinstance(body, dict)
            or set(body) != {"paused", "code", "config"}
            or not isinstance(body["paused"], bool)
            or body["config"] != {}
            or not isinstance(body["code"], str)
            or not re.fullmatch(r"SYN-[A-Z0-9-]{2,60}", body["code"])
        ):
            raise ValueError()

        def save(_current, _episode, _key):
            previous = policy(scope)
            version = previous.revision if previous else 0
            if request.headers.get("If-Match") != f'"{version}"':
                return {"hold_reason": "precondition_failed"}, 412
            row = MessagingPolicy(
                id=uuid4(), facility_id=scope, revision=version + 1, synthetic_only=True, **body
            )
            db.session.add(row)
            db.session.flush()
            audit("messaging.control", "messaging.policy", "messaging_policy", row.id)
            return data(row), 201

        return command("policy", scope, body, save)

    return guarded(run)


def decimal(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,10}(?:\.\d{1,4})?", value):
        raise ValueError()
    return Decimal(value)


@blueprint.route("/messaging/global/budgets", methods=["GET", "POST"])
@blueprint.route("/messaging/facilities/<facility>/budgets", methods=["GET", "POST"])
@require_permission("messaging.control")
def budgets(facility=None):
    def run():
        scope = scope_id(facility)
        context(scope, "messaging.control")

        def data(row):
            used = db.session.scalar(
                select(sa.func.coalesce(sa.func.sum(CostReservation.amount), 0)).where(
                    CostReservation.budget_id == row.id
                )
            )
            return {
                "id": str(row.id),
                "period_start": _iso(row.period_start),
                "period_end": _iso(row.period_end),
                "currency": row.currency,
                "cap": str(row.cap),
                "unit_cost": str(row.unit_cost),
                "reserved": str(used),
                "synthetic_only": True,
            }

        if request.method == "GET":
            rows = db.session.scalars(
                select(MessagingBudget)
                .where(MessagingBudget.facility_id == scope)
                .order_by(MessagingBudget.created_at)
                .limit(100)
            ).all()
            result = [data(row) for row in rows]
            audit("messaging.control", "messaging.budget.read", "messaging_budget")
            db.session.commit()
            return jsonify(data=result)
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) != {
            "period_start",
            "period_end",
            "cap",
            "unit_cost",
            "currency",
            "synthetic_only",
        }:
            raise ValueError()
        start, end = _utc(body["period_start"]), _utc(body["period_end"])
        cap, rate = decimal(body["cap"]), decimal(body["unit_cost"])
        if (
            end <= start
            or rate <= 0
            or body["currency"] != "SYN"
            or body["synthetic_only"] is not True
        ):
            raise ValueError()

        def save(_current, _episode, _key):
            row = MessagingBudget(
                id=uuid4(),
                facility_id=scope,
                period_start=start,
                period_end=end,
                cap=cap,
                unit_cost=rate,
                currency="SYN",
                synthetic_only=True,
            )
            db.session.add(row)
            db.session.flush()
            audit("messaging.control", "messaging.budget", "messaging_budget", row.id)
            return data(row), 201

        return command(
            "budget", scope, {**body, "period_start": _iso(start), "period_end": _iso(end)}, save
        )

    return guarded(run)


@blueprint.post("/messaging/facilities/<facility>/capacity-reservations")
@require_permission("messaging.control")
def capacity(facility):
    def run():
        scope = UUID(facility)
        context(scope, "messaging.control")
        body = request.get_json(silent=True)
        if (
            not isinstance(body, dict)
            or set(body) != {"fixture_encoding", "units"}
            or not isinstance(body["units"], int)
            or isinstance(body["units"], bool)
            or not 1 <= body["units"] <= 10000
        ):
            raise ValueError()
        char = {"gsm7": "A", "gsm7_extension": "^", "unicode": "😀"}.get(body["fixture_encoding"])
        if char is None:
            raise ValueError()
        estimate = estimate_segments("SYNTHETIC CAPACITY TEST " + char * body["units"])

        def save(current, _episode, key):
            recorded = audit("messaging.control", "messaging.capacity", "capacity_simulation")
            db.session.flush()
            result = reserve(
                current,
                scope,
                key,
                estimate.segments,
                db.session.scalar(select(sa.func.clock_timestamp())),
                recorded.id,
            )
            result.update(encoding=estimate.encoding, units=estimate.units)
            return result, 200

        return command("capacity", scope, body, save)

    return guarded(run)
