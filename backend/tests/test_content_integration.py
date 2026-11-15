"""A13/A14 synthetic content lifecycle, independent review and live revalidation."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from test_auth_integration import PASSWORD, _csrf, _login
from test_auth_integration import _fresh_rate_limiter as _fresh_rate_limiter
from test_auth_integration import identity as identity
from test_auth_integration import identity_app as identity_app
from test_pregnancy_integration import owner as owner

from tunza.access import load_access_context
from tunza.config import postgres_url
from tunza.content.catalog import revalidate_fixture_reference, select_fixture_version
from tunza.content.routes import _context
from tunza.extensions import db
from tunza.identity.models import Role, RoleBinding, User
from tunza.identity.passwords import hash_password

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def content_staff(identity, owner):
    with owner.begin() as connection:
        reader_role = uuid4()
        connection.execute(
            text(
                "INSERT INTO roles (id,code,name,scope_kind) VALUES "
                "(:id,'SYN-CONTENT-READER','Synthetic content reader','programme')"
            ),
            {"id": reader_role},
        )
        connection.execute(
            text(
                "INSERT INTO role_permissions (role_id,permission_id) "
                "SELECT :role,id FROM permissions WHERE code = 'content.read'"
            ),
            {"role": reader_role},
        )
    with identity.app.app_context():
        roles = {role.code: role for role in db.session.scalars(select(Role)).all()}
        for login, code in (
            ("content.reader", "SYN-CONTENT-READER"),
            ("content.author2", "AUTHOR"),
            ("content.reviewer", "REVIEWER"),
            ("content.reviewer2", "REVIEWER"),
        ):
            user = User(
                id=uuid4(),
                login_identifier=login,
                display_name="Synthetic content staff",
                password_hash=hash_password(PASSWORD),
                status="active",
            )
            db.session.add(user)
            db.session.flush()
            db.session.add(
                RoleBinding(
                    user_id=user.id,
                    role_id=roles[code].id,
                    scope_kind="programme",
                    facility_id=None,
                    starts_at=datetime.now(UTC),
                    granted_by=identity.user_ids["pa"],
                )
            )
        # A person with both roles still cannot review their own version.
        db.session.add(
            RoleBinding(
                user_id=identity.user_ids["author"],
                role_id=roles["REVIEWER"].id,
                scope_kind="programme",
                starts_at=datetime.now(UTC),
                granted_by=identity.user_ids["pa"],
            )
        )
        db.session.commit()


@pytest.fixture
def clients(identity):
    result = {}
    for name, login in (
        ("author", "tst.author"),
        ("other", "content.author2"),
        ("reviewer", "content.reviewer"),
        ("care", "tst.vht.a"),
    ):
        client = identity.app.test_client()
        _login(client, login)
        result[name] = client
    return result


def mutate(client, path, body, *, version=None, key=None, method="POST"):
    return client.open(
        "/api/v1" + path,
        method=method,
        json=body,
        headers={
            "X-CSRF-Token": _csrf(client),
            "Idempotency-Key": key or str(uuid4()),
            **({"If-Match": f'"{version}"'} if version is not None else {}),
        },
    )


def template(client, **changes):
    response = mutate(
        client,
        "/message-templates",
        {
            "code": "SYN-" + uuid4().hex.upper(),
            "programme": "antenatal",
            "purpose": "weekly_advice",
            "trigger_kind": "weekly",
            "gestational_week": 1,
            **changes,
        },
    )
    assert response.status_code == 201, response.json
    return response.json["data"]


def placeholder(language="en", token="A1B2C3D4"):
    return f"SYNTHETIC PLACEHOLDER - NOT CLINICAL ADVICE - {language} - SYN-{token}"


def draft(client, parent, language="en", token="A1B2C3D4"):
    current = client.get(f"/api/v1/message-templates/{parent['id']}").json["data"]
    response = mutate(
        client,
        "/template-versions",
        {
            "template_id": parent["id"],
            "language": language,
            "text": placeholder(language, token),
            "allowed_parameters": [],
        },
        version=current["version"],
    )
    assert response.status_code == 201, response.json
    return response.json["data"]


def transition(client, row, action, body=None, **options):
    return mutate(
        client,
        f"/template-versions/{row['id']}/{action}",
        body or {},
        version=row["version"],
        **options,
    )


def approved(clients, language="en", parent=None):
    parent = parent or template(clients["author"])
    row = draft(clients["author"], parent, language)
    submitted = transition(clients["author"], row, "submit")
    assert submitted.status_code == 200, submitted.json
    row = submitted.json["data"]
    reviewed = transition(
        clients["reviewer"],
        row,
        "review",
        {
            "decision": "approve",
            "reason": "SYN-FIXTURE-APPROVAL",
            "content_digest": row["content_digest"],
        },
    )
    assert reviewed.status_code == 201, reviewed.json
    return parent, reviewed.json["data"]


WINDOW = {"effective_from": "2020-01-01T00:00:00Z", "effective_to": "2030-01-01T00:00:00Z"}


def test_complete_lifecycle_and_exact_separate_language_review(clients, owner):
    parent, en = approved(clients)
    _, lg = approved(clients, language="lg", parent=parent)
    assert en["content_digest"] != lg["content_digest"]
    assert en["review"]["content_digest"] == en["content_digest"]
    assert en["review"]["reviewer_id"] != en["author_id"]
    for row in (en, lg):
        published = transition(clients["reviewer"], row, "publish", WINDOW)
        assert published.status_code == 200, published.json
        assert published.json["data"]["clinical_signoff"] is False
        assert published.json["data"]["dispatch_eligible"] is False
    published = clients["reviewer"].get(f"/api/v1/template-versions/{en['id']}").json["data"]
    suspended = transition(clients["reviewer"], published, "suspend", {"reason": "SYN-HOLD"})
    assert suspended.status_code == 200
    resumed = transition(clients["reviewer"], suspended.json["data"], "publish", WINDOW)
    assert resumed.status_code == 200
    retired = transition(
        clients["reviewer"], resumed.json["data"], "retire", {"reason": "SYN-RETIRED"}
    )
    assert retired.status_code == 200
    assert (
        transition(clients["reviewer"], retired.json["data"], "publish", WINDOW).status_code == 409
    )
    events = clients["author"].get(f"/api/v1/template-versions/{en['id']}/events")
    assert [row["event_type"] for row in events.json["data"]] == [
        "draft_created",
        "submitted",
        "approved",
        "published",
        "suspended",
        "published",
        "retired",
    ]
    with owner.connect() as connection:
        cipher = connection.scalar(
            text("SELECT body_cipher FROM template_versions WHERE id = :id"), {"id": en["id"]}
        )
        assert placeholder().encode() not in cipher


def test_draft_edit_freezes_after_submission_and_rejection_needs_new_version(clients, owner):
    parent = template(clients["author"])
    row = draft(clients["author"], parent)
    edited = mutate(
        clients["author"],
        f"/template-versions/{row['id']}",
        {"text": placeholder(token="DEADBEEF"), "allowed_parameters": []},
        version=1,
        method="PATCH",
    )
    assert edited.status_code == 200, edited.json
    assert edited.json["data"]["content_digest"] != row["content_digest"]
    row = transition(clients["author"], edited.json["data"], "submit").json["data"]
    assert (
        mutate(
            clients["author"],
            f"/template-versions/{row['id']}",
            {"text": placeholder(), "allowed_parameters": []},
            version=row["version"],
            method="PATCH",
        ).status_code
        == 409
    )
    rejected = transition(
        clients["reviewer"],
        row,
        "review",
        {"decision": "reject", "reason": "SYN-REVISE", "content_digest": row["content_digest"]},
    )
    assert rejected.status_code == 201
    assert (
        transition(clients["reviewer"], rejected.json["data"], "publish", WINDOW).status_code == 409
    )
    replacement = draft(clients["author"], parent)
    assert replacement["revision"] == 2 and replacement["id"] != row["id"]
    with pytest.raises(DBAPIError, match="immutable"), owner.begin() as connection:
        connection.execute(
            text(
                "UPDATE template_versions SET body_cipher = :cipher, "
                "version = version + 1 WHERE id = :id"
            ),
            {"cipher": b"fake", "id": row["id"]},
        )


def test_self_review_denied_even_with_both_roles_and_digest_checked(clients):
    parent = template(clients["author"])
    row = transition(clients["author"], draft(clients["author"], parent), "submit").json["data"]
    assert (
        transition(
            clients["author"],
            row,
            "review",
            {"decision": "approve", "reason": "SYN-SELF", "content_digest": row["content_digest"]},
        ).status_code
        == 403
    )
    assert (
        transition(
            clients["reviewer"],
            row,
            "review",
            {"decision": "approve", "reason": "SYN-STALE", "content_digest": "a" * 64},
        ).status_code
        == 409
    )
    other_language = draft(clients["author"], parent, language="lg")
    assert (
        transition(
            clients["reviewer"],
            row,
            "review",
            {
                "decision": "approve",
                "reason": "SYN-WRONG-LANGUAGE",
                "content_digest": other_language["content_digest"],
            },
        ).status_code
        == 409
    )
    assert transition(clients["reviewer"], row, "publish", WINDOW).status_code == 409


def test_private_drafts_role_separation_and_no_patient_scope_access(clients):
    parent = template(clients["author"])
    row = draft(clients["author"], parent)
    for caller in ("other", "reviewer"):
        result = clients[caller].get(f"/api/v1/template-versions/{row['id']}")
        assert result.status_code == 404 and "data" not in result.json
    assert clients["care"].get("/api/v1/message-templates").status_code == 403
    assert (
        clients["reviewer"]
        .get("/api/v1/patients/" + str(uuid4()) + "?facility_id=" + str(uuid4()))
        .status_code
        == 403
    )
    assert (
        mutate(
            clients["other"],
            "/template-versions",
            {
                "template_id": parent["id"],
                "language": "en",
                "text": placeholder(),
                "allowed_parameters": [],
            },
            version=parent["version"],
        ).status_code
        == 404
    )
    row = transition(clients["author"], row, "submit").json["data"]
    assert clients["reviewer"].get(f"/api/v1/template-versions/{row['id']}").status_code == 200
    assert clients["other"].get(f"/api/v1/template-versions/{row['id']}").status_code == 404


@pytest.mark.parametrize(
    "changes",
    [
        {"text": "Unapproved narrative"},
        {"language": "lg"},
        {"allowed_parameters": ["name"]},
        {"clinical_signoff": True},
    ],
)
def test_only_fixed_labelled_placeholders_no_translations_or_parameters(clients, changes):
    parent = template(clients["author"])
    response = mutate(
        clients["author"],
        "/template-versions",
        {
            "template_id": parent["id"],
            "language": "en",
            "text": placeholder(),
            "allowed_parameters": [],
            **changes,
        },
        version=parent["version"],
    )
    assert response.status_code == 422


def test_publication_overlap_suspension_resume_and_boundary_windows(clients):
    parent, first = approved(clients)
    first = transition(clients["reviewer"], first, "publish", WINDOW).json["data"]
    _, second = approved(clients, parent=parent)
    assert transition(clients["reviewer"], second, "publish", WINDOW).status_code == 409
    suspended = transition(
        clients["reviewer"], first, "suspend", {"reason": "SYN-REPLACEMENT"}
    ).json["data"]
    assert transition(clients["reviewer"], second, "publish", WINDOW).status_code == 200
    assert transition(clients["reviewer"], suspended, "publish", WINDOW).status_code == 409
    assert (
        transition(
            clients["reviewer"], suspended, "publish", {"effective_from": "2030-01-01T00:00:00Z"}
        ).status_code
        == 409
    )
    _, third = approved(clients, parent=parent)
    adjacent = transition(
        clients["reviewer"], third, "publish", {"effective_from": "2030-01-01T00:00:00Z"}
    )
    assert adjacent.status_code == 200, adjacent.json


def test_already_selected_reference_is_invalid_after_suspension_or_resume(clients, identity):
    parent, row = approved(clients)
    published = transition(clients["reviewer"], row, "publish", WINDOW).json["data"]
    instant = datetime(2027, 1, 1, tzinfo=UTC)
    with identity.app.app_context():
        reviewer = db.session.scalar(
            select(User).where(User.login_identifier == "content.reviewer")
        )
        context = load_access_context(reviewer)
        assert _context(context, "content.read")
        reference = select_fixture_version(UUID(parent["id"]), "en", instant)
        assert reference and revalidate_fixture_reference(reference, instant)
        assert select_fixture_version(UUID(parent["id"]), "lg", instant) is None
        db.session.rollback()
    suspended = transition(
        clients["reviewer"], published, "suspend", {"reason": "SYN-STOP-UNSENT"}
    ).json["data"]
    with identity.app.app_context():
        reviewer = db.session.scalar(
            select(User).where(User.login_identifier == "content.reviewer")
        )
        assert _context(load_access_context(reviewer), "content.read")
        assert select_fixture_version(UUID(parent["id"]), "en", instant) is None
        assert not revalidate_fixture_reference(reference, instant)
        db.session.rollback()
    assert transition(clients["reviewer"], suspended, "publish", WINDOW).status_code == 200
    with identity.app.app_context():
        reviewer = db.session.scalar(
            select(User).where(User.login_identifier == "content.reviewer")
        )
        assert _context(load_access_context(reviewer), "content.read")
        assert not revalidate_fixture_reference(reference, instant)
        assert select_fixture_version(UUID(parent["id"]), "en", instant)


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_reviews_or_replay_produce_one_review(clients, identity, owner, same_key):
    parent = template(clients["author"])
    row = transition(clients["author"], draft(clients["author"], parent), "submit").json["data"]
    second = identity.app.test_client()
    _login(second, "content.reviewer")
    barrier = Barrier(2)
    key = str(uuid4())

    def submit(client, request_key):
        barrier.wait(timeout=10)
        return transition(
            client,
            row,
            "review",
            {
                "decision": "approve",
                "reason": "SYN-REVIEW",
                "content_digest": row["content_digest"],
            },
            key=request_key,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(submit, clients["reviewer"], key),
            pool.submit(submit, second, key if same_key else str(uuid4())),
        ]
        results = [future.result(timeout=30) for future in futures]
    assert sorted(result.status_code for result in results) == (
        [201, 201] if same_key else [201, 412]
    ), [r.json for r in results]
    if same_key:
        assert results[0].json == results[1].json
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM content_reviews WHERE version_id = :id"),
                {"id": row["id"]},
            )
            == 1
        )


@pytest.mark.parametrize("action", ["edit", "submit", "review", "publish", "suspend", "retire"])
def test_audit_failure_rolls_back_lifecycle_events_and_key(clients, owner, monkeypatch, action):
    import tunza.content.routes as routes

    parent = template(clients["author"])
    row = draft(clients["author"], parent)
    caller, body = clients["author"], {}
    if action not in {"edit", "submit"}:
        row = transition(clients["author"], row, "submit").json["data"]
        caller = clients["reviewer"]
        body = {
            "decision": "approve",
            "reason": "SYN-REVIEW",
            "content_digest": row["content_digest"],
        }
        if action != "review":
            row = transition(caller, row, "review", body).json["data"]
            body = WINDOW
        if action in {"suspend", "retire"}:
            row = transition(caller, row, "publish", WINDOW).json["data"]
            body = {"reason": "SYN-HOLD"}
    if action == "edit":
        body = {"text": placeholder(token="DEADBEEF"), "allowed_parameters": []}
    key = str(uuid4())

    def fail(*args, **kwargs):
        db.session.flush()
        raise SQLAlchemyError("Synthetic content audit failure")

    with monkeypatch.context() as patch:
        patch.setattr(routes, "_audit", fail)
        path = f"/template-versions/{row['id']}" + ("" if action == "edit" else "/" + action)
        result = mutate(
            caller,
            path,
            body,
            version=row["version"],
            key=key,
            method="PATCH" if action == "edit" else "POST",
        )
        assert result.status_code == 503 and "data" not in result.json
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT version FROM message_templates WHERE id = :id"),
                {"id": row["template_id"]},
            )
            == row["template_version"]
        )
        assert (
            connection.scalar(
                text("SELECT state FROM template_versions WHERE id = :id"), {"id": row["id"]}
            )
            == row["state"]
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM content_events WHERE version_id = :id"),
                {"id": row["id"]},
            )
            == row["version"]
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM content_idempotency WHERE key = :key"), {"key": key}
            )
            == 0
        )


def test_rls_missing_context_and_immutable_review_history(clients, owner):
    _, row = approved(clients)
    runtime = create_engine(postgres_url(os.environ["TEST_DATABASE_URL"]))
    try:
        with runtime.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM template_versions")) == 0
        for table in ("content_reviews", "content_events", "content_idempotency"):
            with pytest.raises(DBAPIError, match="append-only"), owner.begin() as connection:
                connection.execute(text(f"DELETE FROM {table}"))
    finally:
        runtime.dispose()


def test_idempotent_review_replays_original_snapshot_after_publication(clients, owner):
    parent = template(clients["author"])
    row = transition(clients["author"], draft(clients["author"], parent), "submit").json["data"]
    key = str(uuid4())
    body = {"decision": "approve", "reason": "SYN-REVIEW", "content_digest": row["content_digest"]}
    first = transition(clients["reviewer"], row, "review", body, key=key)
    assert first.status_code == 201
    assert transition(clients["reviewer"], first.json["data"], "publish", WINDOW).status_code == 200
    replay = transition(clients["reviewer"], row, "review", body, key=key)
    assert replay.status_code == 201 and replay.json == first.json
    conflict = transition(
        clients["reviewer"], row, "review", {**body, "decision": "reject"}, key=key
    )
    assert conflict.status_code == 409
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM content_events WHERE version_id = :id"),
                {"id": row["id"]},
            )
            == 4
        )


def test_publication_conflicts_are_concurrency_safe(clients, identity, owner):
    parent, one = approved(clients)
    _, two = approved(clients, parent=parent)
    second = identity.app.test_client()
    _login(second, "content.reviewer2")
    barrier = Barrier(2)

    def submit(client, row):
        barrier.wait(timeout=10)
        return transition(client, row, "publish", WINDOW)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit, clients["reviewer"], one), pool.submit(submit, second, two)]
        results = [future.result(timeout=30) for future in futures]
    assert sorted(row.status_code for row in results) == [200, 409], [r.json for r in results]
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM template_versions "
                    "WHERE template_id = :id AND state = 'published'"
                ),
                {"id": parent["id"]},
            )
            == 1
        )


def test_interval_boundaries_and_retirement_are_not_dispatch_authority(clients, identity):
    parent, row = approved(clients)
    published = transition(clients["reviewer"], row, "publish", WINDOW)
    assert published.status_code == 200
    with identity.app.app_context():
        user = db.session.scalar(select(User).where(User.login_identifier == "content.reviewer"))
        assert _context(load_access_context(user), "content.read")
        assert select_fixture_version(UUID(parent["id"]), "en", datetime(2020, 1, 1, tzinfo=UTC))
        assert (
            select_fixture_version(UUID(parent["id"]), "en", datetime(2030, 1, 1, tzinfo=UTC))
            is None
        )
        assert (
            select_fixture_version(UUID(parent["id"]), "lg", datetime(2027, 1, 1, tzinfo=UTC))
            is None
        )
        db.session.rollback()
    retired = transition(
        clients["reviewer"], published.json["data"], "retire", {"reason": "SYN-RETIRE"}
    )
    assert retired.status_code == 200 and retired.json["data"]["dispatch_eligible"] is False


def test_direct_sql_cannot_self_review_or_change_frozen_period(clients, owner):
    parent = template(clients["author"])
    row = transition(clients["author"], draft(clients["author"], parent), "submit").json["data"]
    with pytest.raises(DBAPIError), owner.begin() as connection:
        connection.execute(
            text("SELECT set_config('tunza.actor_id', :actor, true)"), {"actor": row["author_id"]}
        )
        connection.execute(
            text("""
            INSERT INTO content_reviews
                (id,version_id,author_id,reviewer_id,decision,content_digest,reason_cipher,key_version)
            VALUES (:id,:version,:author,:author,'approve',:digest,:cipher,'synthetic-v1')
        """),
            {
                "id": uuid4(),
                "version": row["id"],
                "author": row["author_id"],
                "digest": bytes.fromhex(row["content_digest"]),
                "cipher": b"synthetic",
            },
        )
    reviewed = transition(
        clients["reviewer"],
        row,
        "review",
        {"decision": "approve", "reason": "SYN-REVIEW", "content_digest": row["content_digest"]},
    ).json["data"]
    assert transition(clients["reviewer"], reviewed, "publish", WINDOW).status_code == 200
    with pytest.raises(DBAPIError, match="periods are immutable"), owner.begin() as connection:
        connection.execute(
            text(
                "UPDATE template_versions SET effective_to = '2031-01-01T00:00:00Z', "
                "version = version + 1 WHERE id = :id"
            ),
            {"id": row["id"]},
        )


def test_missing_versions_and_unapproved_template_metadata_are_rejected(clients):
    parent = template(clients["author"])
    result = mutate(
        clients["author"],
        "/template-versions",
        {
            "template_id": parent["id"],
            "language": "en",
            "text": placeholder(),
            "allowed_parameters": [],
        },
    )
    assert result.status_code == 428
    row = draft(clients["author"], parent)
    assert (
        mutate(
            clients["author"], f"/template-versions/{row['id']}/submit", {}, version=0
        ).status_code
        == 412
    )
    assert (
        mutate(
            clients["author"],
            "/message-templates",
            {
                "code": "SYN-INVALID",
                "programme": "postnatal",
                "purpose": "weekly_advice",
                "trigger_kind": "weekly",
                "gestational_week": 1,
            },
        ).status_code
        == 422
    )
    assert (
        mutate(
            clients["author"],
            "/message-templates",
            {
                "code": "SYN-INVALID",
                "programme": "antenatal",
                "purpose": "appointment_reminder",
                "trigger_kind": "appointment_reminder",
                "gestational_week": 1,
            },
        ).status_code
        == 422
    )


def test_revoked_content_binding_denies_replay_and_metadata(clients, owner):
    parent, row = approved(clients)
    key = str(uuid4())
    first = transition(clients["reviewer"], row, "publish", WINDOW, key=key)
    assert first.status_code == 200
    with owner.begin() as connection:
        binding = connection.execute(
            text(
                "SELECT b.id,b.ends_at FROM role_bindings b JOIN users u ON u.id = b.user_id "
                "WHERE u.login_identifier = 'content.reviewer'"
            )
        ).one()
        connection.execute(
            text("UPDATE role_bindings SET ends_at = now() WHERE id = :id"), {"id": binding.id}
        )
    try:
        assert transition(clients["reviewer"], row, "publish", WINDOW, key=key).status_code == 403
        assert clients["reviewer"].get(f"/api/v1/template-versions/{row['id']}").status_code == 403
    finally:
        with owner.begin() as connection:
            connection.execute(
                text("UPDATE role_bindings SET ends_at = :end WHERE id = :id"),
                {"end": binding.ends_at, "id": binding.id},
            )


@pytest.mark.parametrize("action", ["template", "create"])
def test_creation_audit_failure_rolls_back_records_counters_and_replay(
    clients, owner, monkeypatch, action
):
    import tunza.content.routes as routes

    parent = template(clients["author"])
    key = str(uuid4())
    body = (
        {
            "code": "SYN-" + uuid4().hex.upper(),
            "programme": "antenatal",
            "purpose": "appointment_reminder",
            "trigger_kind": "appointment_reminder",
        }
        if action == "template"
        else {
            "template_id": parent["id"],
            "language": "en",
            "text": placeholder(),
            "allowed_parameters": [],
        }
    )
    with owner.connect() as connection:
        before = {
            table: connection.scalar(text(f"SELECT count(*) FROM {table}"))
            for table in ("message_templates", "template_versions", "content_events")
        }

    def fail(*args, **kwargs):
        db.session.flush()
        raise SQLAlchemyError("Synthetic creation audit failure")

    with monkeypatch.context() as patch:
        patch.setattr(routes, "_audit", fail)
        response = mutate(
            clients["author"],
            "/message-templates" if action == "template" else "/template-versions",
            body,
            key=key,
            version=parent["version"] if action == "create" else None,
        )
        assert response.status_code == 503 and "data" not in response.json
    with owner.connect() as connection:
        for table, count in before.items():
            assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == count
        assert (
            connection.scalar(
                text("SELECT version FROM message_templates WHERE id = :id"), {"id": parent["id"]}
            )
            == parent["version"]
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM content_idempotency WHERE key = :key"), {"key": key}
            )
            == 0
        )


def test_creation_and_draft_replays_survive_later_state_without_duplicate_history(clients, owner):
    parent = template(clients["author"])
    body = {
        "template_id": parent["id"],
        "language": "en",
        "text": placeholder(),
        "allowed_parameters": [],
    }
    key = str(uuid4())
    created = mutate(
        clients["author"], "/template-versions", body, key=key, version=parent["version"]
    )
    assert created.status_code == 201
    row = transition(clients["author"], created.json["data"], "submit").json["data"]
    replay = mutate(
        clients["author"], "/template-versions", body, key=key, version=parent["version"]
    )
    assert replay.status_code == 201 and replay.json == created.json
    conflict = mutate(
        clients["author"],
        "/template-versions",
        {**body, "text": placeholder(token="DEADBEEF")},
        key=key,
        version=parent["version"],
    )
    assert conflict.status_code == 409
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM template_versions WHERE template_id = :id"),
                {"id": parent["id"]},
            )
            == 1
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM content_events WHERE version_id = :id"),
                {"id": row["id"]},
            )
            == 2
        )


def test_signed_pagination_binds_actor_filters_and_excludes_private_drafts(clients):
    parent = template(clients["author"])
    one = transition(clients["author"], draft(clients["author"], parent), "submit").json["data"]
    two = transition(clients["author"], draft(clients["author"], parent), "submit").json["data"]
    private = draft(clients["author"], parent)
    path = f"/api/v1/template-versions?limit=1&template_id={parent['id']}&state=submitted"
    first = clients["reviewer"].get(path)
    assert first.status_code == 200
    assert first.json["data"][0]["id"] == one["id"]
    cursor = first.json["page"]["next_cursor"]
    assert cursor
    second = clients["reviewer"].get(path + "&cursor=" + cursor)
    assert second.status_code == 200 and second.json["data"][0]["id"] == two["id"]
    assert second.json["page"]["next_cursor"] is None
    assert clients["author"].get(path + "&cursor=" + cursor).status_code == 422
    assert (
        clients["reviewer"]
        .get(path.replace("submitted", "draft") + "&cursor=" + cursor)
        .status_code
        == 422
    )
    assert clients["reviewer"].get(path + "&cursor=" + cursor + "x").status_code == 422
    visible = (
        clients["reviewer"]
        .get(f"/api/v1/template-versions?template_id={parent['id']}")
        .json["data"]
    )
    assert {row["id"] for row in visible} == {one["id"], two["id"]}
    assert private["id"] not in {row["id"] for row in visible}


def test_read_and_replay_audit_failures_disclose_no_payload(clients, monkeypatch):
    import tunza.content.routes as routes

    parent, row = approved(clients)
    key = str(uuid4())
    assert transition(clients["reviewer"], row, "publish", WINDOW, key=key).status_code == 200

    def fail(*args, **kwargs):
        raise SQLAlchemyError("Synthetic read audit failure")

    monkeypatch.setattr(routes, "_audit", fail)
    for path in (
        "/message-templates",
        f"/message-templates/{parent['id']}",
        "/template-versions",
        f"/template-versions/{row['id']}",
        f"/template-versions/{row['id']}/events",
    ):
        result = clients["reviewer"].get("/api/v1" + path)
        assert result.status_code == 503 and "data" not in result.json
    replay = transition(clients["reviewer"], row, "publish", WINDOW, key=key)
    assert replay.status_code == 503 and "data" not in replay.json


@pytest.mark.parametrize("login", ["content.reader", "tst.author", "content.reviewer"])
@pytest.mark.parametrize("database", ["TEST_DATABASE_URL", "TEST_FIXTURE_DATABASE_URL"])
def test_direct_template_counter_updates_denied_even_with_forged_flags(
    clients, owner, login, database
):
    from tunza.fixture_database import provision_fixture_role

    parent = template(clients["author"])
    if database == "TEST_FIXTURE_DATABASE_URL":
        provision_fixture_role(os.environ["TEST_MIGRATION_DATABASE_URL"], environment="testing")
    runtime = create_engine(postgres_url(os.environ[database]))
    try:
        with pytest.raises(DBAPIError, match="permission denied"), runtime.begin() as connection:
            actor = connection.scalar(
                text("SELECT id FROM users WHERE login_identifier = :login"), {"login": login}
            )
            connection.execute(
                text(
                    "SELECT set_config('tunza.actor_id', :actor, true), "
                    "set_config('tunza.content_operation', 'content.review', true), "
                    "set_config('tunza.content_write', 'true', true)"
                ),
                {"actor": str(actor)},
            )
            assert connection.scalar(text("SELECT tunza_content_permission('content.read')"))
            if login == "content.reader":
                for permission in ("author", "submit", "review", "publish", "suspend"):
                    assert not connection.scalar(
                        text("SELECT tunza_content_permission(:permission)"),
                        {"permission": "content." + permission},
                    )
            assert (
                connection.scalar(
                    text("SELECT version FROM message_templates WHERE id = :id"),
                    {"id": parent["id"]},
                )
                == 1
            )
            assert not connection.scalar(
                text(
                    "SELECT has_column_privilege(current_user, "
                    "'message_templates', 'version', 'UPDATE')"
                )
            )
            assert not connection.scalar(
                text(
                    "SELECT has_function_privilege(current_user, "
                    "'tunza_content_advance_template()', 'EXECUTE')"
                )
            )
            connection.execute(
                text("UPDATE message_templates SET version = version + 1 WHERE id = :id"),
                {"id": parent["id"]},
            )
        with owner.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT version FROM message_templates WHERE id = :id"),
                    {"id": parent["id"]},
                )
                == 1
            )
    finally:
        runtime.dispose()


@pytest.mark.parametrize("audit_kind", ["missing", "wrong_operation", "historical", "reused"])
def test_counter_event_requires_unique_matching_current_transaction_audit(
    clients, owner, audit_kind
):
    parent = template(clients["author"])
    row = draft(clients["author"], parent)
    audit_id = None if audit_kind == "missing" else uuid4()
    audit_sql = text("""
        INSERT INTO audit_events (id,actor_id,request_id,permission,action,resource_type,
                                  resource_id,outcome,metadata)
        VALUES (:id,:actor,:request,'content.author',:action,'content',:resource,'success',
                '{"state":"draft"}'::jsonb)
    """)
    audit_params = {
        "id": audit_id,
        "actor": row["author_id"],
        "request": uuid4(),
        "action": "content.edit",
        "resource": row["id"],
    }
    if audit_kind == "historical":
        with owner.begin() as connection:
            connection.execute(audit_sql, audit_params)
    if audit_kind == "reused":
        with owner.connect() as connection:
            audit_id = connection.scalar(
                text("SELECT audit_event_id FROM content_events WHERE version_id = :id"),
                {"id": row["id"]},
            )
    runtime = create_engine(postgres_url(os.environ["TEST_DATABASE_URL"]))
    try:
        expected_error = (
            "uq_content_event_audit"
            if audit_kind == "reused"
            else "Authorized content operation and current audit required"
        )
        with pytest.raises(DBAPIError, match=expected_error), runtime.begin() as connection:
            connection.execute(
                text("SELECT set_config('tunza.actor_id', :actor, true)"),
                {"actor": row["author_id"]},
            )
            connection.execute(
                text("UPDATE template_versions SET version = version + 1 WHERE id = :id"),
                {"id": row["id"]},
            )
            if audit_kind == "wrong_operation":
                connection.execute(audit_sql, {**audit_params, "action": "content.read"})
            connection.execute(
                text("""
                INSERT INTO content_events
                    (id,version_id,sequence,event_type,actor_id,audit_event_id,
                     event_data_cipher,key_version)
                VALUES (:id,:version,2,'draft_edited',:actor,:audit,:cipher,'synthetic-v1')
            """),
                {
                    "id": uuid4(),
                    "version": row["id"],
                    "actor": row["author_id"],
                    "audit": audit_id,
                    "cipher": b"synthetic-rolled-back-event",
                },
            )
        with owner.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT version FROM message_templates WHERE id = :id"),
                    {"id": parent["id"]},
                )
                == 2
            )
            assert (
                connection.scalar(
                    text("SELECT version FROM template_versions WHERE id = :id"), {"id": row["id"]}
                )
                == 1
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM content_events WHERE version_id = :id"),
                    {"id": row["id"]},
                )
                == 1
            )
    finally:
        runtime.dispose()


def test_template_counter_tracks_author_reviewer_events_once_and_replay_does_not_advance(
    clients, owner
):
    parent = template(clients["author"])
    row = draft(clients["author"], parent)
    assert row["template_version"] == 2
    edited = mutate(
        clients["author"],
        f"/template-versions/{row['id']}",
        {"text": placeholder(token="DEADBEEF"), "allowed_parameters": []},
        version=row["version"],
        method="PATCH",
    ).json["data"]
    assert edited["template_version"] == 3
    row = transition(clients["author"], edited, "submit").json["data"]
    assert row["template_version"] == 4
    review = {
        "decision": "approve",
        "reason": "SYN-REVIEW",
        "content_digest": row["content_digest"],
    }
    key = str(uuid4())
    first = transition(clients["reviewer"], row, "review", review, key=key)
    assert first.status_code == 201 and first.json["data"]["template_version"] == 5
    assert transition(clients["reviewer"], row, "review", review, key=key).json == first.json
    current = first.json["data"]
    for counter, (action, body) in enumerate(
        [
            ("publish", WINDOW),
            ("suspend", {"reason": "SYN-HOLD"}),
            ("publish", WINDOW),
            ("retire", {"reason": "SYN-RETIRE"}),
        ],
        start=6,
    ):
        result = transition(clients["reviewer"], current, action, body)
        assert result.status_code == 200, result.json
        current = result.json["data"]
        assert current["template_version"] == counter
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(DISTINCT e.audit_event_id) FROM content_events e "
                    "JOIN audit_events a ON a.id = e.audit_event_id "
                    "WHERE e.version_id = :id AND a.actor_id = e.actor_id "
                    "AND a.resource_id = e.version_id AND a.outcome = 'success'"
                ),
                {"id": row["id"]},
            )
            == 8
        )


@pytest.mark.parametrize("action", ["create", "review"])
def test_failure_after_counter_trigger_rolls_back_counter_event_audit_and_replay(
    clients, owner, monkeypatch, action
):
    import tunza.content.routes as routes

    parent = template(clients["author"])
    row = draft(clients["author"], parent)
    if action == "review":
        row = transition(clients["author"], row, "submit").json["data"]
    tables = ("content_events", "content_reviews", "audit_events", "content_idempotency")
    with owner.connect() as connection:
        before = {
            table: connection.scalar(text(f"SELECT count(*) FROM {table}")) for table in tables
        }
    encrypt = routes._crypt

    def fail(value, purpose):
        if purpose == "content-response":
            raise ValueError("Synthetic failure after the counter trigger")
        return encrypt(value, purpose)

    monkeypatch.setattr(routes, "_crypt", fail)
    if action == "review":
        response = transition(
            clients["reviewer"],
            row,
            "review",
            {
                "decision": "approve",
                "reason": "SYN-REVIEW",
                "content_digest": row["content_digest"],
            },
        )
    else:
        response = mutate(
            clients["author"],
            "/template-versions",
            {
                "template_id": parent["id"],
                "language": "lg",
                "text": placeholder("lg"),
                "allowed_parameters": [],
            },
            version=row["template_version"],
        )
    assert response.status_code == 503 and "data" not in response.json
    with owner.connect() as connection:
        for table, count in before.items():
            assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == count
        assert (
            connection.scalar(
                text("SELECT version FROM message_templates WHERE id = :id"), {"id": parent["id"]}
            )
            == row["template_version"]
        )
        assert (
            connection.scalar(
                text("SELECT state FROM template_versions WHERE id = :id"), {"id": row["id"]}
            )
            == row["state"]
        )


def test_counter_guard_populated_upgrade_preserves_history_and_revokes_both_logins(
    clients, owner, identity
):
    from tunza.fixture_database import provision_fixture_role

    provision_fixture_role(os.environ["TEST_MIGRATION_DATABASE_URL"], environment="testing")
    parent = template(clients["author"])
    row = draft(clients["author"], parent)
    runner = identity.app.test_cli_runner()
    snapshot_sql = text(
        "SELECT id,version_id,sequence,event_type,actor_id,event_data_cipher,"
        "key_version,created_at FROM content_events ORDER BY id"
    )
    with owner.connect() as connection:
        snapshot = connection.execute(snapshot_sql).all()
    try:
        with identity.app.app_context():
            db.session.remove()
            result = runner.invoke(args=["db", "downgrade", "0014_clinical_content"])
            assert result.exit_code == 0, result.output
        with owner.connect() as connection:
            assert connection.execute(snapshot_sql).all() == snapshot
            for role in ("tunza_runtime", "tunza_fixture_runtime"):
                assert connection.scalar(
                    text(
                        "SELECT has_column_privilege(:role, "
                        "'message_templates', 'version', 'UPDATE')"
                    ),
                    {"role": role},
                )
        with identity.app.app_context():
            result = runner.invoke(args=["db", "upgrade"])
            assert result.exit_code == 0, result.output
            result = runner.invoke(args=["db", "check"])
            assert result.exit_code == 0, result.output
        with owner.connect() as connection:
            assert connection.execute(snapshot_sql).all() == snapshot
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM content_events WHERE audit_event_id IS NOT NULL")
                )
                == 0
            )
            for role in ("tunza_runtime", "tunza_fixture_runtime"):
                assert not connection.scalar(
                    text(
                        "SELECT has_column_privilege(:role, "
                        "'message_templates', 'version', 'UPDATE')"
                    ),
                    {"role": role},
                )
        edited = mutate(
            clients["author"],
            f"/template-versions/{row['id']}",
            {
                "text": placeholder(token="DEADBEEF"),
                "allowed_parameters": [],
            },
            version=1,
            method="PATCH",
        )
        assert edited.status_code == 200, edited.json
        assert edited.json["data"]["template_version"] == 3
        with owner.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM content_events WHERE audit_event_id IS NOT NULL")
                )
                == 1
            )
    finally:
        with identity.app.app_context():
            db.session.remove()
            result = runner.invoke(args=["db", "upgrade"])
            assert result.exit_code == 0, result.output
