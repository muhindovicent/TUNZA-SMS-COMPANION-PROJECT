"""Synthetic placeholder content lifecycle; D07 clinical approval is unconfigured."""

import hashlib
import json
import re
from uuid import UUID, uuid4

import sqlalchemy as sa
from flask import current_app, g, jsonify, request
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from tunza.access import get_access_context, require_permission
from tunza.appointments.routes import _iso, _utc
from tunza.audit import record_audit
from tunza.content import blueprint
from tunza.content.models import (
    ContentEvent,
    ContentIdempotency,
    ContentReview,
    MessageTemplate,
    TemplateVersion,
)
from tunza.extensions import db
from tunza.patients.routes import _advisory_lock_key, _parse_uuid, _validate_idempotency_key
from tunza.services.encryption import decrypt_json, encrypt_json

TEXT = re.compile(r"^SYNTHETIC PLACEHOLDER - NOT CLINICAL ADVICE - (en|lg) - SYN-[0-9A-F]{8,32}$")
CODE = re.compile(r"^SYN-[A-Z0-9][A-Z0-9_-]{2,59}$")
REASON = re.compile(r"^SYN-[A-Z0-9][A-Z0-9-]{1,59}$")
STATES = {"draft", "submitted", "approved", "rejected", "published", "suspended", "retired"}
PERMISSIONS = {
    "template": "content.author",
    "create": "content.author",
    "edit": "content.author",
    "submit": "content.submit",
    "review": "content.review",
    "publish": "content.publish",
    "suspend": "content.suspend",
    "retire": "content.suspend",
}


def _error(code, message, status):
    return jsonify(error={"code": code, "message": message, "request_id": g.request_id}), status


def _audit(context, permission, action, resource=None, outcome="success", state=None):
    return record_audit(
        permission=permission,
        action=action,
        resource_type="content",
        actor_id=context.user.id,
        resource_id=resource,
        request_id=g.request_id,
        outcome=outcome,
        metadata={"state": state} if state else None,
    )


def _deny(context, permission):
    _audit(context, permission, "content.access", outcome="denied")
    db.session.commit()
    return _error("not_found", "The request could not be completed.", 404)


def _context(context, permission):
    db.session.execute(
        sa.text(
            "SELECT set_config('tunza.actor_id',:actor,true), "
            "set_config('tunza.facility_id','',true), set_config('tunza.patient_id','',true)"
        ),
        {"actor": str(context.user.id)},
    )
    return bool(db.session.scalar(select(sa.func.tunza_content_permission(permission))))


def _crypt(value, purpose):
    return encrypt_json(
        value,
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


def content_digest(template, version, text):
    return hashlib.sha256(
        json.dumps(
            {
                "template_id": str(template.id),
                "code": template.code,
                "programme": template.programme,
                "purpose": template.purpose,
                "trigger_kind": template.trigger_kind,
                "gestational_week": template.gestational_week,
                "version_id": str(version.id),
                "revision": version.revision,
                "language": version.language,
                "author_id": str(version.author_id),
                "text": text,
                "allowed_parameters": [],
                "approval_scope": "synthetic_fixture_only",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).digest()


def _body(row, template):
    value = _decrypt(row.body_cipher, row.key_version, "content-version")
    if value.get("allowed_parameters") != [] or not _placeholder(value.get("text"), row.language):
        raise ValueError("Invalid fixture content")
    if content_digest(template, row, value["text"]) != row.content_digest:
        raise ValueError("Content digest mismatch")
    return value


def _placeholder(value, language):
    match = TEXT.fullmatch(value) if isinstance(value, str) else None
    return bool(match and match[1] == language)


def _template_data(row):
    return {
        "id": str(row.id),
        "code": row.code,
        "programme": row.programme,
        "purpose": row.purpose,
        "trigger_kind": row.trigger_kind,
        "gestational_week": row.gestational_week,
        "created_by": str(row.created_by),
        "version": row.version,
        "synthetic_only": True,
        "clinical_signoff": False,
        "dispatch_eligible": False,
    }


def _version_data(row, template):
    body = _body(row, template)
    review = db.session.scalar(select(ContentReview).where(ContentReview.version_id == row.id))
    return {
        "id": str(row.id),
        "template_id": str(row.template_id),
        "template_code": template.code,
        "language": row.language,
        "revision": row.revision,
        "author_id": str(row.author_id),
        "text": body["text"],
        "allowed_parameters": [],
        "content_digest": row.content_digest.hex(),
        "state": row.state,
        "version": row.version,
        "template_version": template.version,
        "effective_from": _iso(row.effective_from),
        "effective_to": _iso(row.effective_to),
        "approval_scope": "synthetic_fixture_only",
        "clinical_signoff": False,
        "dispatch_eligible": False,
        "dispatch_hold_reason": "D07_unconfigured",
        "review": {
            "id": str(review.id),
            "decision": review.decision,
            "reviewer_id": str(review.reviewer_id),
            "reason": _decrypt(review.reason_cipher, review.key_version, "content-review")[
                "reason"
            ],
            "content_digest": review.content_digest.hex(),
            "clinical_signoff": False,
        }
        if review
        else None,
    }


def _response(data, status=200):
    response = jsonify(data=data)
    if isinstance(data, dict) and "version" in data:
        response.headers["ETag"] = f'"{data["version"]}"'
    return response, status


def _normalize(action, payload):
    if not isinstance(payload, dict):
        raise ValueError("A JSON object is required.")
    if action == "template":
        if set(payload) - {"code", "programme", "purpose", "trigger_kind", "gestational_week"}:
            raise ValueError()
        code, purpose, trigger, week = (
            payload.get(k) for k in ("code", "purpose", "trigger_kind", "gestational_week")
        )
        if (
            not isinstance(code, str)
            or not CODE.fullmatch(code)
            or payload.get("programme") != "antenatal"
        ):
            raise ValueError()
        if purpose == "weekly_advice" and trigger == "weekly":
            if not isinstance(week, int) or isinstance(week, bool) or not 0 <= week <= 2147483647:
                raise ValueError()
        elif (
            purpose != "appointment_reminder"
            or trigger != "appointment_reminder"
            or week is not None
        ):
            raise ValueError()
        return {
            "code": code,
            "programme": "antenatal",
            "purpose": purpose,
            "trigger_kind": trigger,
            "gestational_week": week,
        }
    if action in {"create", "edit"}:
        required = (
            {"template_id", "language", "text", "allowed_parameters"}
            if action == "create"
            else {"text", "allowed_parameters"}
        )
        if (
            set(payload) != required
            or payload["allowed_parameters"] != []
            or not isinstance(payload["allowed_parameters"], list)
        ):
            raise ValueError()
        result = {"text": payload["text"], "allowed_parameters": []}
        if action == "create":
            template = _parse_uuid(payload["template_id"])
            language = payload["language"]
            if (
                template is None
                or not isinstance(language, str)
                or language not in {"en", "lg"}
                or not _placeholder(payload["text"], language)
            ):
                raise ValueError()
            result.update(template_id=str(template), language=language)
        elif not isinstance(payload["text"], str) or not TEXT.fullmatch(payload["text"]):
            raise ValueError()
        return result
    if action == "submit":
        if payload:
            raise ValueError()
        return {}
    if action == "publish":
        if set(payload) - {"effective_from", "effective_to"}:
            raise ValueError()
        start = _utc(payload.get("effective_from"))
        end = _utc(payload["effective_to"]) if payload.get("effective_to") is not None else None
        if end is not None and end <= start:
            raise ValueError()
        return {"effective_from": _iso(start), "effective_to": _iso(end)}
    if set(payload) != (
        {"decision", "reason", "content_digest"} if action == "review" else {"reason"}
    ):
        raise ValueError()
    reason = payload.get("reason")
    if not isinstance(reason, str) or not REASON.fullmatch(reason):
        raise ValueError()
    if action == "review":
        if not isinstance(payload["decision"], str) or payload["decision"] not in {
            "approve",
            "reject",
        }:
            raise ValueError()
        digest = payload["content_digest"]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise ValueError()
        return {"decision": payload["decision"], "reason": reason, "content_digest": digest.lower()}
    return {"reason": reason}


def _event(row, action, actor, audit_id, reason=None):
    db.session.add(
        ContentEvent(
            id=uuid4(),
            version_id=row.id,
            sequence=row.version,
            event_type=action,
            actor_id=actor,
            audit_event_id=audit_id,
            event_data_cipher=_crypt(
                {
                    "digest": row.content_digest.hex(),
                    "reason": reason,
                    "effective_from": _iso(row.effective_from),
                    "effective_to": _iso(row.effective_to),
                },
                "content-event",
            ),
            key_version=current_app.config["PATIENT_KEY_VERSION"],
        )
    )


def _write(action, target=None):
    context = get_access_context()
    permission = PERMISSIONS[action]
    operation = "content." + action
    try:
        if not _context(context, permission):
            return _deny(context, permission)
        try:
            body = _normalize(action, request.get_json(silent=True))
        except (ValueError, TypeError):
            return _error(
                "validation_failed",
                "Only labelled synthetic placeholders and supported fixture metadata are allowed.",
                422,
            )
        key = _validate_idempotency_key()
        if key is None:
            return _error("validation_failed", "A valid Idempotency-Key is required.", 422)
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "actor": str(context.user.id),
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
        cached = db.session.scalar(
            select(ContentIdempotency).where(
                ContentIdempotency.actor_id == context.user.id,
                ContentIdempotency.operation == operation,
                ContentIdempotency.key == key,
            )
        )
        if cached:
            if cached.request_hash != fingerprint:
                db.session.rollback()
                return _error("conflict", "The idempotency key was already used.", 409)
            data, status = (
                _decrypt(cached.response_cipher, cached.key_version, "content-response"),
                cached.response_status,
            )
            _audit(context, permission, "content.replay", cached.version_id or cached.template_id)
            db.session.commit()
            return _response(data, status)
        row = None
        if action == "template":
            template = MessageTemplate(id=uuid4(), created_by=context.user.id, version=1, **body)
            db.session.add(template)
        else:
            if action == "create":
                template_id = UUID(body["template_id"])
            else:
                row = db.session.get(TemplateVersion, target)
                if row is None:
                    return _deny(context, permission)
                template_id = row.template_id
            # Counter UPDATE is trigger-only, so parent serialization uses a
            # transaction advisory lock instead of SELECT FOR UPDATE privileges.
            db.session.execute(
                sa.text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": _advisory_lock_key("content.template", template_id, template_id)},
            )
            template = db.session.scalar(
                select(MessageTemplate)
                .where(MessageTemplate.id == template_id)
                .execution_options(populate_existing=True)
            )
            if template is None or (action == "create" and template.created_by != context.user.id):
                return _deny(context, permission)
            if row:
                row = db.session.scalar(
                    select(TemplateVersion)
                    .where(TemplateVersion.id == target)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if row is None:
                    return _deny(context, permission)
                _body(row, template)  # authenticated plaintext must still match the exact digest
            match = request.headers.get("If-Match")
            if match is None:
                return _error("precondition_required", "An If-Match version is required.", 428)
            if match != f'"{row.version if row else template.version}"':
                return _error("precondition_failed", "The content changed. Refresh it.", 412)
            if action == "create":
                revision = (
                    db.session.scalar(
                        select(sa.func.max(TemplateVersion.revision)).where(
                            TemplateVersion.template_id == template.id,
                            TemplateVersion.language == body["language"],
                        )
                    )
                    or 0
                ) + 1
                row = TemplateVersion(
                    id=uuid4(),
                    template_id=template.id,
                    language=body["language"],
                    author_id=context.user.id,
                    revision=revision,
                    state="draft",
                    version=1,
                    allowed_parameters=[],
                    key_version=current_app.config["PATIENT_KEY_VERSION"],
                )
                row.body_cipher = _crypt(
                    {"text": body["text"], "allowed_parameters": []}, "content-version"
                )
                row.content_digest = content_digest(template, row, body["text"])
                db.session.add(row)
            else:
                allowed = {
                    "edit": {"draft"},
                    "submit": {"draft"},
                    "review": {"submitted"},
                    "publish": {"approved", "suspended"},
                    "suspend": {"published"},
                    "retire": {"published", "suspended"},
                }[action]
                if row.state not in allowed:
                    return _error(
                        "conflict",
                        "The content state does not allow this action; "
                        "corrections need a new version.",
                        409,
                    )
                if action in {"edit", "submit"} and row.author_id != context.user.id:
                    return _deny(context, permission)
                if action == "edit":
                    if not _placeholder(body["text"], row.language):
                        return _error(
                            "validation_failed",
                            "The fixture label must match the version language.",
                            422,
                        )
                    row.body_cipher = _crypt(body, "content-version")
                    row.content_digest = content_digest(template, row, body["text"])
                    row.key_version = current_app.config["PATIENT_KEY_VERSION"]
                elif action == "submit":
                    row.state = "submitted"
                elif action == "review":
                    if row.author_id == context.user.id:
                        return _error(
                            "forbidden",
                            "Independent fixture review requires a different person.",
                            403,
                        )
                    if body["content_digest"] != row.content_digest.hex():
                        return _error(
                            "conflict", "The review digest does not match this exact version.", 409
                        )
                    review = ContentReview(
                        id=uuid4(),
                        version_id=row.id,
                        author_id=row.author_id,
                        reviewer_id=context.user.id,
                        decision=body["decision"],
                        content_digest=row.content_digest,
                        reason_cipher=_crypt({"reason": body["reason"]}, "content-review"),
                        key_version=current_app.config["PATIENT_KEY_VERSION"],
                        clinical_signoff=False,
                    )
                    db.session.add(review)
                    db.session.flush()
                    row.state = "approved" if body["decision"] == "approve" else "rejected"
                elif action == "publish":
                    start = _utc(body["effective_from"])
                    end = _utc(body["effective_to"]) if body["effective_to"] else None
                    if row.effective_from and (
                        row.effective_from != start or row.effective_to != end
                    ):
                        return _error(
                            "conflict",
                            "A suspended fixture can only resume its original publication period.",
                            409,
                        )
                    row.effective_from, row.effective_to, row.state = start, end, "published"
                else:
                    row.state = "suspended" if action == "suspend" else "retired"
                row.version += 1
        db.session.flush()
        audit = _audit(
            context,
            permission,
            operation,
            row.id if row else template.id,
            state=row.state if row else None,
        )
        db.session.flush()
        if row:
            event_type = (
                "draft_created"
                if action == "create"
                else "draft_edited"
                if action == "edit"
                else row.state
            )
            _event(row, event_type, context.user.id, audit.id, body.get("reason"))
            db.session.flush()
            db.session.refresh(template, ["version"])
        data = _version_data(row, template) if row else _template_data(template)
        status = 201 if action in {"template", "create", "review"} else 200
        db.session.add(
            ContentIdempotency(
                id=uuid4(),
                actor_id=context.user.id,
                template_id=template.id,
                version_id=row.id if row else None,
                operation=operation,
                key=key,
                request_hash=fingerprint,
                response_cipher=_crypt(data, "content-response"),
                response_status=status,
                key_version=current_app.config["PATIENT_KEY_VERSION"],
            )
        )
        db.session.commit()
        return _response(data, status)
    except IntegrityError:
        db.session.rollback()
        return _error(
            "conflict",
            "The fixture, key or publication period conflicts with an existing record.",
            409,
        )
    except (SQLAlchemyError, ValueError):
        db.session.rollback()
        return _error("service_unavailable", "The content request could not be completed.", 503)


@blueprint.post("/message-templates")
@require_permission("content.author")
def create_template():
    return _write("template")


@blueprint.post("/template-versions")
@require_permission("content.author")
def create_version():
    return _write("create")


@blueprint.patch("/template-versions/<uuid:target>")
@require_permission("content.author")
def edit_version(target):
    return _write("edit", target)


@blueprint.post("/template-versions/<uuid:target>/submit")
@require_permission("content.submit")
def submit(target):
    return _write("submit", target)


@blueprint.post("/template-versions/<uuid:target>/review")
@require_permission("content.review")
def review(target):
    return _write("review", target)


@blueprint.post("/template-versions/<uuid:target>/publish")
@require_permission("content.publish")
def publish(target):
    return _write("publish", target)


@blueprint.post("/template-versions/<uuid:target>/suspend")
@require_permission("content.suspend")
def suspend(target):
    return _write("suspend", target)


@blueprint.post("/template-versions/<uuid:target>/retire")
@require_permission("content.suspend")
def retire(target):
    return _write("retire", target)


def _read(target, kind):
    context = get_access_context()
    try:
        if not _context(context, "content.read"):
            return _deny(context, "content.read")
        if kind == "template":
            row = db.session.get(MessageTemplate, target)
            if row is None:
                return _deny(context, "content.read")
            data = _template_data(row)
        else:
            row = db.session.get(TemplateVersion, target)
            if row is None:
                return _deny(context, "content.read")
            template = db.session.get(MessageTemplate, row.template_id)
            if kind == "events":
                events = db.session.scalars(
                    select(ContentEvent)
                    .where(ContentEvent.version_id == target)
                    .order_by(ContentEvent.sequence)
                ).all()
                data = [
                    {
                        "id": str(event.id),
                        "sequence": event.sequence,
                        "event_type": event.event_type,
                        "actor_id": str(event.actor_id),
                        "created_at": _iso(event.created_at),
                        "data": _decrypt(
                            event.event_data_cipher, event.key_version, "content-event"
                        ),
                    }
                    for event in events
                ]
            else:
                data = _version_data(row, template)
        _audit(context, "content.read", "content.read", target)
        db.session.commit()
        return _response(data)
    except (SQLAlchemyError, ValueError):
        db.session.rollback()
        return _error("service_unavailable", "The content request could not be completed.", 503)


@blueprint.get("/message-templates/<uuid:target>")
@require_permission("content.read")
def template_detail(target):
    return _read(target, "template")


@blueprint.get("/template-versions/<uuid:target>")
@require_permission("content.read")
def version_detail(target):
    return _read(target, "version")


@blueprint.get("/template-versions/<uuid:target>/events")
@require_permission("content.read")
def history(target):
    return _read(target, "events")


def _list(versions=False):
    context = get_access_context()
    try:
        if not _context(context, "content.read"):
            return _deny(context, "content.read")
        model = TemplateVersion if versions else MessageTemplate
        query = select(model)
        try:
            limit = int(request.args.get("limit", "25"))
            if not 1 <= limit <= 100:
                raise ValueError()
            filters = [str(context.user.id), versions]
            for name, allowed in [
                ("state", STATES),
                ("language", {"en", "lg"}),
                ("template_id", None),
            ]:
                value = request.args.get(name) if versions else None
                filters.append(value)
                if value:
                    if allowed is not None and value not in allowed:
                        raise ValueError()
                    query = query.where(
                        getattr(model, name) == (UUID(value) if name == "template_id" else value)
                    )
            serializer = URLSafeSerializer(current_app.config["SECRET_KEY"], salt="content-cursor")
            if request.args.get("cursor"):
                decoded = serializer.loads(request.args["cursor"])
                if decoded["scope"] != filters:
                    raise ValueError()
                query = query.where(
                    sa.tuple_(model.created_at, model.id)
                    > sa.tuple_(_utc(decoded["time"]), UUID(decoded["id"]))
                )
        except (ValueError, TypeError, KeyError, BadSignature):
            return _error("validation_failed", "Invalid content filters or cursor.", 422)
        rows = db.session.scalars(query.order_by(model.created_at, model.id).limit(limit + 1)).all()
        data = [
            _version_data(row, db.session.get(MessageTemplate, row.template_id))
            if versions
            else _template_data(row)
            for row in rows[:limit]
        ]
        cursor = (
            serializer.dumps(
                {
                    "scope": filters,
                    "time": _iso(rows[limit - 1].created_at),
                    "id": str(rows[limit - 1].id),
                }
            )
            if len(rows) > limit
            else None
        )
        _audit(context, "content.read", "content.list")
        db.session.commit()
        return jsonify(data=data, page={"next_cursor": cursor})
    except (SQLAlchemyError, ValueError):
        db.session.rollback()
        return _error("service_unavailable", "The content request could not be completed.", 503)


@blueprint.get("/message-templates")
@require_permission("content.read")
def templates():
    return _list()


@blueprint.get("/template-versions")
@require_permission("content.read")
def versions():
    return _list(versions=True)
