"""Opt-in end-to-end authentication, RBAC and facility-isolation tests (PostgreSQL only)."""

from __future__ import annotations

import json
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from tunza import create_app
from tunza.access import accessible_facility_ids, facility_scope_allows, load_access_context
from tunza.config import postgres_url
from tunza.extensions import db
from tunza.identity.models import (
    AuditEvent,
    AuthSession,
    Facility,
    FacilityMembership,
    Permission,
    Role,
    RoleBinding,
    User,
)
from tunza.identity.passwords import hash_password
from tunza.identity.sessions import hash_session_token

PASSWORD = "synthetic-test-password"


@pytest.fixture(scope="module")
def identity_app():
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated empty local tunza_*_test database.")
    url = postgres_url(value, "TEST_DATABASE_URL")
    if (
        url.host not in {"127.0.0.1", "localhost", "::1"}
        or not url.database.startswith("tunza_")
        or not url.database.endswith("_test")
    ):
        pytest.fail("Integration tests require a dedicated local tunza_*_test database.")
    migration_value = os.environ.get("TEST_MIGRATION_DATABASE_URL")
    if not migration_value:
        pytest.fail("Set TEST_MIGRATION_DATABASE_URL to the separate disposable owner login.")
    migration_url = postgres_url(migration_value, "TEST_MIGRATION_DATABASE_URL")
    if (migration_url.host, migration_url.port, migration_url.database) != (
        url.host,
        url.port,
        url.database,
    ) or migration_url.username == url.username:
        pytest.fail(
            "Test runtime and migration users must be distinct and target the same database."
        )

    application = create_app(
        {
            "APP_ENV": "testing",
            "SECRET_KEY": secrets.token_urlsafe(48),
            "DATABASE_URL": value,
            "MIGRATION_DATABASE_URL": migration_value,
            "SESSION_COOKIE_SECURE": False,
        }
    )
    runner = application.test_cli_runner()
    with application.app_context():
        if inspect(db.engine).has_table("alembic_version"):
            result = runner.invoke(args=["db", "downgrade", "base"])
            assert result.exit_code == 0, result.output
        result = runner.invoke(args=["db", "upgrade"])
        assert result.exit_code == 0, result.output
        db.session.remove()
    yield application
    with application.app_context():
        db.session.remove()
        result = runner.invoke(args=["db", "downgrade", "base"])
        assert result.exit_code == 0, result.output
        db.engine.dispose()


@pytest.fixture(scope="module")
def identity(identity_app):
    with identity_app.app_context():
        roles = {role.code: role for role in db.session.scalars(select(Role)).all()}
        for code in ("VHT", "FA", "AUTHOR", "PA"):
            assert code in roles, f"missing seeded role {code}"
        assert (
            db.session.scalar(select(Permission).where(Permission.code == "facility.read"))
            is not None
        )

        facilities: dict[str, Facility] = {}
        for code, name, active in (
            ("TST-A", "Test Clinic A", True),
            ("TST-B", "Test Clinic B", True),
            ("TST-C", "Test Clinic C", True),
            ("TST-D", "Inactive Clinic", False),
        ):
            facility = Facility(code=code, name=name, timezone="Africa/Kampala", active=active)
            db.session.add(facility)
            db.session.flush()
            facilities[code] = facility

        def make_user(login: str, display_name: str, status: str = "active") -> User:
            user = User(
                login_identifier=login,
                display_name=display_name,
                password_hash=hash_password(PASSWORD),
                status=status,
            )
            db.session.add(user)
            db.session.flush()
            return user

        users = {
            "pa": make_user("tst.pa", "Test Platform Admin"),
            "vht_a": make_user("tst.vht.a", "Test VHT A"),
            "vht_b": make_user("tst.vht.b", "Test VHT B"),
            "author": make_user("tst.author", "Test Author"),
            "disabled": make_user("tst.disabled", "Test Disabled", status="disabled"),
            "bumpable": make_user("tst.bump", "Test Bumpable"),
            "revocable": make_user("tst.revocable", "Test Revocable"),
            "multi": make_user("tst.multi", "Test Multi Role"),
            "mixed": make_user("tst.mixed", "Test Mixed Scope"),
            "stale": make_user("tst.stale", "Test Stale Binding"),
            "lapsed": make_user("tst.lapsed", "Test Lapsed Membership"),
            "mismatch": make_user("tst.mismatch", "Test Scope Mismatch"),
        }

        now = datetime.now(UTC)

        def bind(
            user: User,
            role_code: str,
            scope_kind: str,
            facility: Facility | None = None,
            *,
            starts_at: datetime | None = None,
            ends_at: datetime | None = None,
        ):
            binding = RoleBinding(
                user_id=user.id,
                role_id=roles[role_code].id,
                facility_id=facility.id if facility is not None else None,
                scope_kind=scope_kind,
                starts_at=starts_at or now,
                ends_at=ends_at,
                granted_by=users["pa"].id,
            )
            db.session.add(binding)
            db.session.flush()
            return binding

        def member(
            user: User,
            facility: Facility,
            *,
            status: str = "active",
            starts_at: datetime | None = None,
            ends_at: datetime | None = None,
        ) -> FacilityMembership:
            membership = FacilityMembership(
                user_id=user.id,
                facility_id=facility.id,
                status=status,
                starts_at=starts_at or now,
                ends_at=ends_at,
            )
            db.session.add(membership)
            db.session.flush()
            return membership

        bind(users["pa"], "PA", "platform")
        bind(users["vht_a"], "VHT", "facility", facilities["TST-A"])
        member(users["vht_a"], facilities["TST-A"])
        bind(users["vht_b"], "VHT", "facility", facilities["TST-B"])
        member(users["vht_b"], facilities["TST-B"])
        bind(users["author"], "AUTHOR", "programme")
        revocable_binding = bind(users["revocable"], "VHT", "facility", facilities["TST-A"])
        revocable_membership = member(users["revocable"], facilities["TST-A"])

        bind(users["multi"], "VHT", "facility", facilities["TST-A"])
        member(users["multi"], facilities["TST-A"])
        bind(users["multi"], "FA", "facility", facilities["TST-B"])
        member(users["multi"], facilities["TST-B"])

        bind(users["mixed"], "PA", "platform")
        bind(users["mixed"], "VHT", "facility", facilities["TST-A"])
        member(users["mixed"], facilities["TST-A"])

        bind(
            users["stale"],
            "VHT",
            "facility",
            facilities["TST-A"],
            starts_at=now - timedelta(days=1),
            ends_at=now - timedelta(seconds=1),
        )
        member(users["stale"], facilities["TST-A"])

        bind(users["lapsed"], "VHT", "facility", facilities["TST-A"])
        member(users["lapsed"], facilities["TST-A"], status="ended")

        bind(users["mismatch"], "VHT", "platform")
        bind(users["mismatch"], "PA", "facility", facilities["TST-A"])
        member(users["mismatch"], facilities["TST-A"])

        snapshot = SimpleNamespace(
            app=identity_app,
            facility_ids={code: facility.id for code, facility in facilities.items()},
            user_ids={name: user.id for name, user in users.items()},
            revocable_membership_id=revocable_membership.id,
            revocable_binding_id=revocable_binding.id,
            password=PASSWORD,
        )
        db.session.commit()
        return snapshot


@pytest.fixture(autouse=True)
def _fresh_rate_limiter(identity_app):
    limiter = identity_app.extensions["tunza.login_rate_limiter"]
    limiter.clear()
    yield
    limiter.clear()


@pytest.fixture
def client(identity_app):
    return identity_app.test_client()


def _csrf(client) -> str:
    response = client.get("/api/v1/auth/csrf")
    assert response.status_code == 200
    return response.json["data"]["csrf_token"]


def _login(client, identifier: str, password: str = PASSWORD, *, expect: int = 200):
    response = client.post(
        "/api/v1/auth/login",
        json={"login_identifier": identifier, "password": password},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert response.status_code == expect, response.json
    return response


def _session_record(token: str) -> AuthSession | None:
    return db.session.scalar(
        select(AuthSession).where(AuthSession.token_hash == hash_session_token(token))
    )


def test_synthetic_patient_registration_and_read(client, identity):
    _login(client, "tst.vht.a")
    facility_id = str(identity.facility_ids["TST-A"])
    payload = {
        "facility_id": facility_id,
        "local_reference": "SYN-ADULT-REGISTRY-TEST",
        "demographics": {},
        "language": "en",
    }
    headers = {"X-CSRF-Token": _csrf(client), "Idempotency-Key": str(uuid4())}
    response = client.post("/api/v1/patients", json=payload, headers=headers)
    assert response.status_code == 201, response.json
    replay = client.post("/api/v1/patients", json=payload, headers=headers)
    assert replay.status_code == 201
    assert replay.json == response.json
    conflict = client.post("/api/v1/patients", json={**payload, "language": "lg"}, headers=headers)
    assert conflict.status_code == 409
    patient_id = response.json["data"]["patient"]["id"]
    detail = client.get(f"/api/v1/patients/{patient_id}?facility_id={facility_id}")
    assert detail.status_code == 200
    assert detail.json["data"]["id"] == patient_id
    runtime_url = postgres_url(os.environ["TEST_DATABASE_URL"])
    runtime = create_engine(runtime_url, pool_size=1, max_overflow=0)
    try:
        with runtime.connect() as connection:
            assert connection.execute(text("SELECT session_user")).scalar_one() == "tunza_runtime"
            assert not connection.execute(
                text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
            ).scalar_one()
            assert (
                connection.execute(
                    text("SELECT count(*) FROM facility_patient_profiles WHERE patient_id = :id"),
                    {"id": patient_id},
                ).scalar_one()
                == 0
            )
            connection.execute(
                text(
                    "SELECT set_config('tunza.actor_id', :actor, true), "
                    "set_config('tunza.facility_id', :facility, true)"
                ),
                {"actor": str(identity.user_ids["vht_a"]), "facility": facility_id},
            )
            assert (
                connection.execute(
                    text("SELECT count(*) FROM facility_patient_profiles WHERE patient_id = :id"),
                    {"id": patient_id},
                ).scalar_one()
                == 1
            )
            connection.execute(
                text("SELECT set_config('tunza.facility_id', :facility, true)"),
                {"facility": str(identity.facility_ids["TST-B"])},
            )
            assert (
                connection.execute(
                    text("SELECT count(*) FROM facility_patient_profiles WHERE patient_id = :id"),
                    {"id": patient_id},
                ).scalar_one()
                == 0
            )
        with runtime.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM facility_patient_profiles WHERE patient_id = :id"),
                    {"id": patient_id},
                ).scalar_one()
                == 0
            )
    finally:
        runtime.dispose()
    with identity.app.app_context():
        with db.engine.connect() as connection:
            assert connection.execute(text("SELECT current_user")).scalar_one() == "tunza_runtime"
            assert (
                connection.execute(
                    text("SELECT count(*) FROM facility_patient_profiles WHERE patient_id = :id"),
                    {"id": patient_id},
                ).scalar_one()
                == 0
            )
            connection.execute(
                text(
                    "SELECT set_config('tunza.actor_id', :actor, true), "
                    "set_config('tunza.facility_id', :facility, true)"
                ),
                {"actor": str(identity.user_ids["vht_a"]), "facility": facility_id},
            )
            assert (
                connection.execute(
                    text("SELECT count(*) FROM facility_patient_profiles WHERE patient_id = :id"),
                    {"id": patient_id},
                ).scalar_one()
                == 1
            )
            connection.execute(
                text("SELECT set_config('tunza.facility_id', :facility, true)"),
                {"facility": str(identity.facility_ids["TST-B"])},
            )
            assert (
                connection.execute(
                    text("SELECT count(*) FROM facility_patient_profiles WHERE patient_id = :id"),
                    {"id": patient_id},
                ).scalar_one()
                == 0
            )
        with db.engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM facility_patient_profiles WHERE patient_id = :id"),
                    {"id": patient_id},
                ).scalar_one()
                == 0
            )

    other_staff = identity.app.test_client()
    _login(other_staff, "tst.revocable")
    denied = other_staff.get(f"/api/v1/patients/{patient_id}?facility_id={facility_id}")
    assert denied.status_code == 404
    other_facility = identity.app.test_client()
    _login(other_facility, "tst.vht.b")
    denied = other_facility.get(f"/api/v1/patients/{patient_id}?facility_id={facility_id}")
    assert denied.status_code == 404
    with identity.app.app_context():
        events = db.session.scalars(
            select(AuditEvent).where(
                AuditEvent.action == "patient.access", AuditEvent.outcome == "denied"
            )
        ).all()
        assert {event.event_metadata.get("reason") for event in events} >= {
            "unavailable",
            "facility_scope",
        }
        assert all(event.resource_id is None and event.facility_id is None for event in events)


def test_concurrent_synthetic_registration_retries(identity):
    clients = [identity.app.test_client(), identity.app.test_client()]
    for client in clients:
        _login(client, "tst.vht.a")
    csrf = [_csrf(client) for client in clients]
    key = str(uuid4())
    payload = {
        "facility_id": str(identity.facility_ids["TST-A"]),
        "local_reference": "SYN-ADULT-CONCURRENT-TEST",
        "demographics": {},
        "language": "en",
    }
    barrier = Barrier(2)

    def submit(index):
        barrier.wait(timeout=10)
        response = clients[index].post(
            "/api/v1/patients",
            json=payload,
            headers={"X-CSRF-Token": csrf[index], "Idempotency-Key": key},
        )
        return response.status_code, response.json

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, range(2)))
    assert [result[0] for result in results] == [201, 201]
    assert results[0][1] == results[1][1]


def test_patient_audit_failure_rolls_back_and_suppresses_response(client, identity, monkeypatch):
    import tunza.patients.routes as patient_routes

    _login(client, "tst.vht.a")
    facility = str(identity.facility_ids["TST-A"])
    payload = {
        "facility_id": facility,
        "local_reference": "SYN-ADULT-AUDIT-TEST",
        "demographics": {},
        "language": "en",
    }
    headers = {"X-CSRF-Token": _csrf(client), "Idempotency-Key": str(uuid4())}

    def fail_audit(**_kwargs):
        raise SQLAlchemyError("synthetic audit failure")

    with monkeypatch.context() as patch:
        patch.setattr(patient_routes, "_record_patient_audit", fail_audit)
        assert client.post("/api/v1/patients", json=payload, headers=headers).status_code == 503
    created = client.post("/api/v1/patients", json=payload, headers=headers)
    assert created.status_code == 201
    patient_id = created.json["data"]["patient"]["id"]
    with monkeypatch.context() as patch:
        patch.setattr(patient_routes, "_record_patient_audit", fail_audit)
        response = client.get(f"/api/v1/patients/{patient_id}?facility_id={facility}")
        assert response.status_code == 503
        assert "data" not in response.json


def test_expired_patient_assignment_denies_detail_and_replay(client, identity):
    _login(client, "tst.vht.a")
    facility = str(identity.facility_ids["TST-A"])
    payload = {
        "facility_id": facility,
        "local_reference": "SYN-ADULT-EXPIRY-TEST",
        "demographics": {},
        "language": "en",
    }
    headers = {"X-CSRF-Token": _csrf(client), "Idempotency-Key": str(uuid4())}
    response = client.post("/api/v1/patients", json=payload, headers=headers)
    assert response.status_code == 201
    data = response.json["data"]
    owner = create_engine(
        postgres_url(
            os.environ.get("TEST_MIGRATION_DATABASE_URL") or os.environ["TEST_DATABASE_URL"]
        )
    )
    try:
        with owner.begin() as connection:
            connection.execute(
                text(
                    "UPDATE participant_assignments SET valid_from = now() - interval '2 hours', "
                    "valid_to = now() - interval '1 hour' WHERE id = :id"
                ),
                {"id": data["participant_assignment_id"]},
            )
    finally:
        owner.dispose()
    assert (
        client.get(f"/api/v1/patients/{data['patient']['id']}?facility_id={facility}").status_code
        == 404
    )
    assert client.post("/api/v1/patients", json=payload, headers=headers).status_code == 404


def test_login_success_returns_session_cookie_and_identity(client, identity):
    response = _login(client, "tst.vht.a")
    assert response.json["data"] == {
        "state": "active",
        "user_id": str(identity.user_ids["vht_a"]),
    }
    set_cookie = response.headers["Set-Cookie"]
    assert "tunza_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=Lax" in set_cookie
    assert "Path=/" in set_cookie

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    data = me.json["data"]
    assert data["display_name"] == "Test VHT A"
    assert data["state"] == "active"
    assert data["facilities"] == [
        {
            "id": str(identity.facility_ids["TST-A"]),
            "code": "TST-A",
            "name": "Test Clinic A",
        }
    ]
    assert data["bindings"] == [
        {
            "role_code": "VHT",
            "role_name": "VHT",
            "scope_kind": "facility",
            "facility_id": str(identity.facility_ids["TST-A"]),
        }
    ]
    assert "facility.read" in data["permissions"]

    with identity.app.app_context():
        event = db.session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "login",
                AuditEvent.outcome == "success",
                AuditEvent.actor_id == identity.user_ids["vht_a"],
            )
        )
        assert event is not None
        assert event.resource_id is not None
        assert PASSWORD not in json.dumps(event.event_metadata)


def test_login_failures_are_generic_and_audited(client, identity):
    unknown = _login(client, "tst.missing", "wrong-password", expect=401)
    wrong = _login(client, "tst.vht.a", "wrong-password", expect=401)
    disabled = _login(client, "tst.disabled", PASSWORD, expect=401)

    generic = "Sign-in details could not be verified."
    for response in (unknown, wrong, disabled):
        assert response.json["error"]["code"] == "invalid_credentials"
        assert response.json["error"]["message"] == generic
    assert client.get_cookie("tunza_session") is None

    with identity.app.app_context():
        events = list(
            db.session.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.action == "login",
                    AuditEvent.outcome == "denied",
                )
                .order_by(AuditEvent.created_at.desc())
                .limit(3)
            ).all()
        )
        assert len(events) == 3
        reasons = {event.event_metadata.get("reason") for event in events}
        assert reasons <= {"invalid_credentials", "account_inactive"}
        for event in events:
            assert PASSWORD not in json.dumps(event.event_metadata)


def test_login_rate_limiting_blocks_after_repeated_failures(client, identity_app):
    limiter = identity_app.extensions["tunza.login_rate_limiter"]
    for _ in range(identity_app.config["LOGIN_RATE_LIMIT_ATTEMPTS"]):
        limiter.record_failure("login:ip:127.0.0.1")
        limiter.record_failure("login:id:tst.vht.a")

    response = _login(client, "tst.vht.a", PASSWORD, expect=429)
    assert response.json["error"]["code"] == "rate_limited"
    assert (
        1
        <= int(response.headers["Retry-After"])
        <= identity_app.config["LOGIN_RATE_LIMIT_WINDOW_SECONDS"]
    )


def test_protected_endpoints_reject_anonymous_requests(client):
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/facilities").status_code == 401
    assert client.post("/api/v1/auth/logout").status_code == 403


def test_logout_revokes_the_session_and_requires_fresh_session_csrf(client, identity):
    stale_pre_auth = _csrf(client)
    _login(client, "tst.vht.a")
    first_token = client.get_cookie("tunza_session").value

    rejected = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": stale_pre_auth})
    assert rejected.status_code == 403
    assert client.get("/api/v1/auth/me").status_code == 200

    response = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": _csrf(client)})
    assert response.status_code == 204

    with identity.app.app_context():
        record = _session_record(first_token)
        assert record is not None
        assert record.state == "revoked"
        assert record.revoked_at is not None

    client.set_cookie("tunza_session", first_token, samesite="Lax")
    assert client.get("/api/v1/auth/me").status_code == 401

    _login(client, "tst.vht.a")
    second_token = client.get_cookie("tunza_session").value
    assert second_token
    assert second_token != first_token


def test_absolute_session_expiry_rejects_requests(client, identity):
    _login(client, "tst.vht.a")
    token = client.get_cookie("tunza_session").value
    with identity.app.app_context():
        record = _session_record(token)
        assert record is not None
        record.expires_at = datetime.now(UTC) - timedelta(seconds=5)
        db.session.commit()
    assert client.get("/api/v1/auth/me").status_code == 401


def test_idle_session_expiry_rejects_requests(client, identity_app, identity):
    _login(client, "tst.vht.a")
    token = client.get_cookie("tunza_session").value
    idle = identity_app.config["SESSION_IDLE_TIMEOUT_SECONDS"]
    with identity.app.app_context():
        record = _session_record(token)
        assert record is not None
        record.last_seen_at = datetime.now(UTC) - timedelta(seconds=idle + 60)
        db.session.commit()
    assert client.get("/api/v1/auth/me").status_code == 401


def test_security_version_change_revokes_existing_session(client, identity):
    _login(client, "tst.bump")
    with identity.app.app_context():
        user = db.session.get(User, identity.user_ids["bumpable"])
        assert user is not None
        user.security_version += 1
        db.session.commit()
    assert client.get("/api/v1/auth/me").status_code == 401


def test_author_role_cannot_read_facilities(client, identity):
    _login(client, "tst.author")
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert "facility.read" not in me.json["data"]["permissions"]

    response = client.get("/api/v1/facilities")
    assert response.status_code == 403

    with identity.app.app_context():
        event = db.session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "access",
                AuditEvent.outcome == "denied",
                AuditEvent.actor_id == identity.user_ids["author"],
            )
        )
        assert event is not None
        assert event.permission == "facility.read"


def test_vht_sees_only_their_own_facility(client, identity):
    _login(client, "tst.vht.a")
    listing = client.get("/api/v1/facilities")
    assert listing.status_code == 200
    assert [item["code"] for item in listing.json["data"]] == ["TST-A"]

    own = client.get(f"/api/v1/facilities/{identity.facility_ids['TST-A']}")
    assert own.status_code == 200
    assert own.json["data"]["code"] == "TST-A"

    other = client.get(f"/api/v1/facilities/{identity.facility_ids['TST-B']}")
    assert other.status_code == 404
    missing = client.get(f"/api/v1/facilities/{uuid4()}")
    assert missing.status_code == 404


def test_vht_b_sees_only_their_own_facility(client, identity):
    _login(client, "tst.vht.b")
    listing = client.get("/api/v1/facilities")
    assert listing.status_code == 200
    assert [item["code"] for item in listing.json["data"]] == ["TST-B"]
    assert client.get(f"/api/v1/facilities/{identity.facility_ids['TST-A']}").status_code == 404


def test_platform_admin_sees_every_active_facility(client, identity):
    _login(client, "tst.pa")
    listing = client.get("/api/v1/facilities")
    assert listing.status_code == 200
    assert [item["code"] for item in listing.json["data"]] == [
        "TST-A",
        "TST-B",
        "TST-C",
    ]
    inactive = client.get(f"/api/v1/facilities/{identity.facility_ids['TST-D']}")
    assert inactive.status_code == 404


def test_revoking_membership_and_binding_removes_facility_access(client, identity):
    _login(client, "tst.revocable")
    listing = client.get("/api/v1/facilities")
    assert [item["code"] for item in listing.json["data"]] == ["TST-A"]

    with identity.app.app_context():
        membership = db.session.get(FacilityMembership, identity.revocable_membership_id)
        assert membership is not None
        membership.status = "ended"
        db.session.commit()
    assert client.get("/api/v1/facilities").json["data"] == []
    assert client.get(f"/api/v1/facilities/{identity.facility_ids['TST-A']}").status_code == 404

    with identity.app.app_context():
        binding = db.session.get(RoleBinding, identity.revocable_binding_id)
        assert binding is not None
        expired_at = datetime.now(UTC)
        binding.starts_at = expired_at - timedelta(days=1)
        binding.ends_at = expired_at - timedelta(seconds=1)
        db.session.commit()
    assert client.get("/api/v1/facilities").status_code == 403


def test_audit_events_reject_updates_and_deletes(client, identity):
    _login(client, "tst.vht.a")
    with identity.app.app_context():
        event = db.session.scalar(
            select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(1)
        )
        assert event is not None
        with pytest.raises(DBAPIError, match="permission denied|append-only"):
            db.session.execute(
                text("UPDATE audit_events SET outcome = 'success' WHERE id = :event_id"),
                {"event_id": event.id},
            )
        db.session.rollback()
        with pytest.raises(DBAPIError, match="permission denied|append-only"):
            db.session.execute(
                text("DELETE FROM audit_events WHERE id = :event_id"),
                {"event_id": event.id},
            )
        db.session.rollback()


def test_multiple_roles_do_not_leak_permissions_across_facilities(client, identity):
    _login(client, "tst.multi")
    listing = client.get("/api/v1/facilities")
    assert listing.status_code == 200
    assert [item["code"] for item in listing.json["data"]] == ["TST-A", "TST-B"]
    me = client.get("/api/v1/auth/me")
    assert {item["role_code"] for item in me.json["data"]["bindings"]} == {"VHT", "FA"}

    with identity.app.app_context():
        user = db.session.get(User, identity.user_ids["multi"])
        context = load_access_context(user)
        facility_a = identity.facility_ids["TST-A"]
        facility_b = identity.facility_ids["TST-B"]

        assert accessible_facility_ids(context, "patient.read") == {facility_a}
        assert facility_scope_allows(context, facility_a, "patient.read") is True
        assert facility_scope_allows(context, facility_b, "patient.read") is False

        assert accessible_facility_ids(context, "membership.manage") == {facility_b}
        assert facility_scope_allows(context, facility_b, "membership.manage") is True
        assert facility_scope_allows(context, facility_a, "membership.manage") is False


def test_platform_binding_does_not_amplify_facility_scoped_permissions(client, identity):
    _login(client, "tst.mixed")
    listing = client.get("/api/v1/facilities")
    assert listing.status_code == 200
    assert [item["code"] for item in listing.json["data"]] == ["TST-A", "TST-B", "TST-C"]

    with identity.app.app_context():
        user = db.session.get(User, identity.user_ids["mixed"])
        context = load_access_context(user)
        facility_a = identity.facility_ids["TST-A"]
        facility_b = identity.facility_ids["TST-B"]
        facility_c = identity.facility_ids["TST-C"]
        all_active = {facility_a, facility_b, facility_c}

        assert accessible_facility_ids(context, "facility.read") == all_active
        assert accessible_facility_ids(context, "membership.manage") == all_active
        assert facility_scope_allows(context, facility_b, "membership.manage") is True

        assert "patient.read" in context.permissions
        assert accessible_facility_ids(context, "patient.read") == {facility_a}
        assert facility_scope_allows(context, facility_a, "patient.read") is True
        assert facility_scope_allows(context, facility_b, "patient.read") is False


def test_expired_binding_yields_no_grants_and_fails_closed(client, identity):
    _login(client, "tst.stale")
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json["data"]["bindings"] == []
    assert me.json["data"]["permissions"] == []
    assert client.get("/api/v1/facilities").status_code == 403

    with identity.app.app_context():
        user = db.session.get(User, identity.user_ids["stale"])
        context = load_access_context(user)
        assert context.grants == ()
        assert accessible_facility_ids(context, "facility.read") == set()
        assert (
            facility_scope_allows(context, identity.facility_ids["TST-A"], "facility.read") is False
        )


def test_ended_membership_blocks_facility_scoped_permissions(client, identity):
    _login(client, "tst.lapsed")
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert "patient.read" in me.json["data"]["permissions"]
    assert client.get("/api/v1/facilities").json["data"] == []
    assert client.get(f"/api/v1/facilities/{identity.facility_ids['TST-A']}").status_code == 404

    with identity.app.app_context():
        user = db.session.get(User, identity.user_ids["lapsed"])
        context = load_access_context(user)
        assert "patient.read" in context.permissions
        assert accessible_facility_ids(context, "patient.read") == set()
        assert (
            facility_scope_allows(context, identity.facility_ids["TST-A"], "patient.read") is False
        )


def test_scope_mismatched_bindings_are_excluded_and_fail_closed(client, identity):
    _login(client, "tst.mismatch")
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json["data"]["bindings"] == []
    assert me.json["data"]["permissions"] == []
    assert client.get("/api/v1/facilities").status_code == 403

    with identity.app.app_context():
        user = db.session.get(User, identity.user_ids["mismatch"])
        context = load_access_context(user)
        assert context.grants == ()
        assert accessible_facility_ids(context, "facility.read") == set()
        assert (
            facility_scope_allows(context, identity.facility_ids["TST-A"], "facility.read") is False
        )


def _register_synthetic_patient(client, facility_id, reference_suffix="TEST"):
    """Register a synthetic patient and return patient_id."""
    key = str(uuid4())
    payload = {
        "facility_id": facility_id,
        "local_reference": f"SYN-ADULT-ELIG-{reference_suffix}",
        "demographics": {},
        "language": "en",
    }
    response = client.post(
        "/api/v1/patients",
        json=payload,
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": key},
    )
    assert response.status_code == 201, response.json
    return response.json["data"]["patient"]["id"]


def _create_consent_fixtures(
    identity_app,
    facility_id,
    actor_id,
    patient_id=None,
    notice_code_suffix="TEST",
):
    """Create a valid approved consent notice and evidence using the owner connection.
    Optionally associates evidence with a patient for patient-specific selection."""
    owner = create_engine(
        postgres_url(
            os.environ.get("TEST_MIGRATION_DATABASE_URL") or os.environ["TEST_DATABASE_URL"]
        )
    )
    try:
        with owner.begin() as connection:
            connection.execute(
                text(
                    "SELECT set_config('tunza.actor_id', :actor, true), "
                    "set_config('tunza.facility_id', :facility, true)"
                ),
                {"actor": str(actor_id), "facility": facility_id},
            )
            notice_id = connection.execute(
                sa.text(
                    "INSERT INTO consent_notice_versions "
                    "(id, notice_code, revision, language, text_cipher, purpose_codes, "
                    "status, effective_from, effective_to, version) "
                    "VALUES (:id, :notice_code, 1, 'en', :cipher, "
                    "ARRAY['messaging'], 'approved', now() - interval '1 day', "
                    "now() + interval '1 year', 1) "
                    "RETURNING id"
                ),
                {
                    "id": uuid4(),
                    "notice_code": f"SYN-CONSENT-{notice_code_suffix}",
                    "cipher": b"synthetic-notice-cipher",
                },
            ).scalar_one()
            connection.execute(
                text("SELECT set_config('tunza.patient_id', :patient, true)"),
                {"patient": str(patient_id) if patient_id else ""},
            )
            evidence_id = connection.execute(
                sa.text(
                    "INSERT INTO protected_evidence "
                    "(id, patient_id, facility_id, storage_reference, digest, "
                    "media_type, recorded_by, version) "
                    "VALUES (:id, :patient_id, :facility_id, :ref, :digest, "
                    ":media_type, :recorded_by, 1) "
                    "RETURNING id"
                ),
                {
                    "id": uuid4(),
                    "patient_id": patient_id,
                    "facility_id": facility_id,
                    "ref": f"synthetic-evidence-{notice_code_suffix}",
                    "digest": b"synthetic-digest",
                    "media_type": "application/pdf",
                    "recorded_by": actor_id,
                },
            ).scalar_one()
        return notice_id, evidence_id
    finally:
        owner.dispose()


def test_eligibility_record_defaults_to_ineligible(client, identity):
    _login(client, "tst.vht.a")
    facility_id = str(identity.facility_ids["TST-A"])
    patient_id = _register_synthetic_patient(client, facility_id, "elig-default")

    response = client.post(
        f"/api/v1/patients/{patient_id}/eligibility?facility_id={facility_id}",
        json={
            "method": "documentary",
            "result": "uncertain",
            "checked_at": "2026-01-15T10:00:00Z",
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 201, response.json
    assert response.json["data"]["result"] == "uncertain"

    with identity.app.app_context():
        event_count = db.session.execute(
            sa.select(sa.func.count())
            .select_from(sa.text("audit_events"))
            .where(sa.text("action = 'eligibility.record'"))
        ).scalar_one()
        assert event_count >= 1


def test_eligibility_rejects_unassigned_staff(client, identity):
    _login(client, "tst.vht.a")
    facility_id = str(identity.facility_ids["TST-A"])
    patient_id = _register_synthetic_patient(client, facility_id, "elig-unassigned")

    client_b = identity.app.test_client()
    _login(client_b, "tst.vht.b")

    response = client_b.post(
        f"/api/v1/patients/{patient_id}/eligibility?facility_id={facility_id}",
        json={
            "method": "documentary",
            "result": "verified",
            "checked_at": "2026-01-15T10:00:00Z",
        },
        headers={"X-CSRF-Token": _csrf(client_b), "Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 404


def test_consent_grant_withdraw_and_history(client, identity):
    _login(client, "tst.vht.a")
    facility_id = str(identity.facility_ids["TST-A"])
    patient_id = _register_synthetic_patient(client, facility_id, "consent-flow")

    notice_id, evidence_id = _create_consent_fixtures(
        identity.app, facility_id, identity.user_ids["vht_a"], patient_id, "FLOW"
    )

    grant_response = client.post(
        f"/api/v1/patients/{patient_id}/consent-events?facility_id={facility_id}",
        json={
            "purpose": "messaging",
            "decision": "grant",
            "notice_version_id": str(notice_id),
            "method": "documentary",
            "language": "en",
            "evidence_id": str(evidence_id),
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": str(uuid4())},
    )
    assert grant_response.status_code == 201, grant_response.json
    assert grant_response.json["data"]["decision"] == "grant"
    assert grant_response.json["data"]["purpose"] == "messaging"

    withdraw_response = client.post(
        f"/api/v1/patients/{patient_id}/consent-events?facility_id={facility_id}",
        json={
            "purpose": "messaging",
            "decision": "withdraw",
            "method": "documentary",
            "language": "en",
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": str(uuid4())},
    )
    assert withdraw_response.status_code == 201, withdraw_response.json
    assert withdraw_response.json["data"]["decision"] == "withdraw"

    events_response = client.get(
        f"/api/v1/patients/{patient_id}/consent-events?facility_id={facility_id}",
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert events_response.status_code == 200
    events = events_response.json["data"]
    assert len(events) >= 2
    decisions = {event["decision"] for event in events}
    assert "grant" in decisions
    assert "withdraw" in decisions


def test_cross_facility_consent_denied(client, identity):
    _login(client, "tst.vht.a")
    facility_a = str(identity.facility_ids["TST-A"])
    patient_id = _register_synthetic_patient(client, facility_a, "cross-facility")

    client_b = identity.app.test_client()
    _login(client_b, "tst.vht.b")

    response = client_b.post(
        f"/api/v1/patients/{patient_id}/eligibility?facility_id={facility_a}",
        json={
            "method": "documentary",
            "result": "verified",
            "checked_at": "2026-01-15T10:00:00Z",
        },
        headers={"X-CSRF-Token": _csrf(client_b), "Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 404


def test_consent_audit_failure_rolls_back(client, identity, monkeypatch):
    import tunza.patients.routes as patient_routes

    _login(client, "tst.vht.a")
    facility_id = str(identity.facility_ids["TST-A"])
    patient_id = _register_synthetic_patient(client, facility_id, "audit-fail")

    def fail_audit(**_kwargs):
        raise SQLAlchemyError("synthetic audit failure")

    with monkeypatch.context() as patch:
        patch.setattr(patient_routes, "_record_patient_audit", fail_audit)
        response = client.post(
            f"/api/v1/patients/{patient_id}/eligibility?facility_id={facility_id}",
            json={
                "method": "documentary",
                "result": "verified",
                "checked_at": "2026-01-15T10:00:00Z",
            },
            headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": str(uuid4())},
        )
        assert response.status_code == 503

    response = client.post(
        f"/api/v1/patients/{patient_id}/eligibility?facility_id={facility_id}",
        json={
            "method": "documentary",
            "result": "verified",
            "checked_at": "2026-01-15T10:00:00Z",
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 201, response.json


def test_cross_patient_evidence_denied(client, identity):
    """Cross-patient evidence access should be denied."""
    _login(client, "tst.vht.a")
    facility_id = str(identity.facility_ids["TST-A"])

    patient_a_id = _register_synthetic_patient(client, facility_id, "cross-ev-a")
    patient_b_id = _register_synthetic_patient(client, facility_id, "cross-ev-b")

    registered = client.post(
        f"/api/v1/patients/{patient_a_id}/evidence?facility_id={facility_id}",
        json={
            "storage_reference": "synthetic-cross-a",
            "digest": "a" * 64,
            "media_type": "application/pdf",
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": str(uuid4())},
    )
    assert registered.status_code == 201, registered.json
    evidence_id = registered.json["data"]["id"]

    # Try to access patient A's evidence as patient B's reader - should fail
    # because evidence is scoped to patient A only
    response = client.get(
        f"/api/v1/patients/{patient_b_id}/evidence/{evidence_id}?facility_id={facility_id}",
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert response.status_code == 404


def test_grant_requires_valid_notice_and_evidence(client, identity):
    """Consent grant without valid notice/evidence references should fail closed."""
    _login(client, "tst.vht.a")
    facility_id = str(identity.facility_ids["TST-A"])
    patient_id = _register_synthetic_patient(client, facility_id, "grant-deny")

    # Grant without notice_version_id should fail
    response = client.post(
        f"/api/v1/patients/{patient_id}/consent-events?facility_id={facility_id}",
        json={
            "purpose": "messaging",
            "decision": "grant",
            "method": "documentary",
            "language": "en",
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 422

    # Grant with non-existent notice_version_id should fail
    response = client.post(
        f"/api/v1/patients/{patient_id}/consent-events?facility_id={facility_id}",
        json={
            "purpose": "messaging",
            "decision": "grant",
            "notice_version_id": str(uuid4()),
            "method": "documentary",
            "language": "en",
            "evidence_id": str(uuid4()),
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 422


def test_consent_events_append_only(client, identity):
    """Consent events should be append-only at the database level."""
    _login(client, "tst.vht.a")
    facility_id = str(identity.facility_ids["TST-A"])
    patient_id = _register_synthetic_patient(client, facility_id, "append-only")

    # Create a consent event through the API (existing visible row)
    notice_id, evidence_id = _create_consent_fixtures(
        identity.app, facility_id, identity.user_ids["vht_a"], patient_id, "APPEND"
    )
    create_response = client.post(
        f"/api/v1/patients/{patient_id}/consent-events?facility_id={facility_id}",
        json={
            "purpose": "messaging",
            "decision": "grant",
            "notice_version_id": str(notice_id),
            "method": "documentary",
            "language": "en",
            "evidence_id": str(evidence_id),
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": str(uuid4())},
    )
    assert create_response.status_code == 201, create_response.json
    event_id = create_response.json["data"]["id"]
    original_decision = create_response.json["data"]["decision"]

    with identity.app.app_context():
        from tunza.access import load_access_context
        from tunza.identity.models import User
        from tunza.patients.security import establish_patient_context

        user = db.session.get(User, identity.user_ids["vht_a"])
        context = load_access_context(user)
        establish_patient_context(context, facility_id)

        # Try to UPDATE the consent event - should fail due to append-only trigger
        try:
            db.session.execute(
                sa.text("UPDATE consent_events SET decision = 'withdraw' WHERE id = :id"),
                {"id": event_id},
            )
            db.session.commit()
            raise AssertionError("UPDATE should have failed due to append-only trigger")
        except DBAPIError:
            db.session.rollback()

        # Re-establish context after rollback
        establish_patient_context(context, facility_id)

        # Explicit affected-row check: row must be unchanged
        unchanged = db.session.scalar(
            sa.text("SELECT decision FROM consent_events WHERE id = :id"),
            {"id": event_id},
        )
        assert unchanged == original_decision, "Row was modified despite append-only trigger"

        # Try to DELETE the consent event - should fail due to append-only trigger
        try:
            db.session.execute(
                sa.text("DELETE FROM consent_events WHERE id = :id"),
                {"id": event_id},
            )
            db.session.commit()
            raise AssertionError("DELETE should have failed due to append-only trigger")
        except DBAPIError:
            db.session.rollback()

        # Re-establish context after rollback
        establish_patient_context(context, facility_id)

        # Explicit affected-row check: row must still exist
        count = db.session.scalar(
            sa.text("SELECT count(*) FROM consent_events WHERE id = :id"),
            {"id": event_id},
        )
        assert count == 1, "Row was deleted despite append-only trigger"


def test_concurrent_eligibility_sequence_safety(identity):
    """Concurrent eligibility checks for the same patient+facility get distinct sequences."""
    _login_client = identity.app.test_client()
    _login(_login_client, "tst.vht.a")
    facility_id = str(identity.facility_ids["TST-A"])
    patient_id = _register_synthetic_patient(_login_client, facility_id, "concurrent-elig")

    client_b = identity.app.test_client()
    _login(client_b, "tst.vht.a")

    csrf_a = _csrf(_login_client)
    csrf_b = _csrf(client_b)

    barrier = Barrier(2)
    results = {}

    def submit(client, csrf, label):
        barrier.wait(timeout=10)
        response = client.post(
            f"/api/v1/patients/{patient_id}/eligibility?facility_id={facility_id}",
            json={
                "method": "documentary",
                "result": "verified",
                "checked_at": "2026-01-15T10:00:00Z",
            },
            headers={"X-CSRF-Token": csrf, "Idempotency-Key": str(uuid4())},
        )
        results[label] = (response.status_code, response.json)

    with ThreadPoolExecutor(max_workers=2) as executor:
        executor.submit(submit, _login_client, csrf_a, "a")
        executor.submit(submit, client_b, csrf_b, "b")

    statuses = {results[k][0] for k in results}
    assert statuses == {201}, results
    sequences = {results[k][1]["data"]["sequence"] for k in results}
    assert len(sequences) == 2, f"Expected distinct sequences, got {sequences}"


def test_concurrent_consent_sequence_safety(identity):
    """Concurrent consent events for the same patient+facility get distinct sequences."""
    _login_client = identity.app.test_client()
    _login(_login_client, "tst.vht.a")
    facility_id = str(identity.facility_ids["TST-A"])
    patient_id = _register_synthetic_patient(_login_client, facility_id, "concurrent-consent")

    notice_id, evidence_id = _create_consent_fixtures(
        identity.app, facility_id, identity.user_ids["vht_a"], patient_id, "CONCURRENT"
    )

    client_b = identity.app.test_client()
    _login(client_b, "tst.vht.a")

    csrf_a = _csrf(_login_client)
    csrf_b = _csrf(client_b)

    barrier = Barrier(2)
    results = {}

    def submit(client, csrf, label):
        barrier.wait(timeout=10)
        response = client.post(
            f"/api/v1/patients/{patient_id}/consent-events?facility_id={facility_id}",
            json={
                "purpose": "messaging",
                "decision": "grant",
                "method": "documentary",
                "language": "en",
                "notice_version_id": str(notice_id),
                "evidence_id": str(evidence_id),
            },
            headers={"X-CSRF-Token": csrf, "Idempotency-Key": str(uuid4())},
        )
        results[label] = (response.status_code, response.json)

    with ThreadPoolExecutor(max_workers=2) as executor:
        executor.submit(submit, _login_client, csrf_a, "a")
        executor.submit(submit, client_b, csrf_b, "b")

    statuses = {results[k][0] for k in results}
    assert statuses == {201}, results
    sequences = {results[k][1]["data"]["event_sequence"] for k in results}
    assert len(sequences) == 2, f"Expected distinct sequences, got {sequences}"


def test_consent_withdraw_requires_facility_scope(client, identity):
    """Legitimate same-facility withdrawal succeeds with facility-scoped consent.withdraw."""
    _login(client, "tst.vht.a")
    facility_id = str(identity.facility_ids["TST-A"])
    patient_id = _register_synthetic_patient(client, facility_id, "withdraw-scope")

    # Register a grant first
    notice_id, evidence_id = _create_consent_fixtures(
        identity.app, facility_id, identity.user_ids["vht_a"], patient_id, "WITHDRAW"
    )

    grant_response = client.post(
        f"/api/v1/patients/{patient_id}/consent-events?facility_id={facility_id}",
        json={
            "purpose": "messaging",
            "decision": "grant",
            "notice_version_id": str(notice_id),
            "method": "documentary",
            "language": "en",
            "evidence_id": str(evidence_id),
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": str(uuid4())},
    )
    assert grant_response.status_code == 201, grant_response.json

    # vht.a has consent.withdraw facility-scoped to TST-A -> same-facility withdrawal succeeds
    withdraw_response = client.post(
        f"/api/v1/patients/{patient_id}/consent-events?facility_id={facility_id}",
        json={
            "purpose": "messaging",
            "decision": "withdraw",
            "method": "documentary",
            "language": "en",
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": str(uuid4())},
    )
    assert withdraw_response.status_code == 201, withdraw_response.json
    assert withdraw_response.json["data"]["decision"] == "withdraw"


def test_consent_withdraw_denied_without_facility_scope(client, identity):
    """Cross-facility withdrawal denied without facility-scoped consent.withdraw."""
    # Register patient at TST-A as vht.a
    _login(client, "tst.vht.a")
    facility_a = str(identity.facility_ids["TST-A"])
    patient_id = _register_synthetic_patient(client, facility_a, "withdraw-cross")

    # Register a grant first (as vht.a at TST-A)
    notice_id, evidence_id = _create_consent_fixtures(
        identity.app, facility_a, identity.user_ids["vht_a"], patient_id, "WITHDRAW-CROSS"
    )
    client.post(
        f"/api/v1/patients/{patient_id}/consent-events?facility_id={facility_a}",
        json={
            "purpose": "messaging",
            "decision": "grant",
            "notice_version_id": str(notice_id),
            "method": "documentary",
            "language": "en",
            "evidence_id": str(evidence_id),
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": str(uuid4())},
    )

    # vht.b has consent.withdraw via VHT role but only facility-scoped to TST-B, not TST-A
    client_b = identity.app.test_client()
    _login(client_b, "tst.vht.b")

    withdraw_response = client_b.post(
        f"/api/v1/patients/{patient_id}/consent-events?facility_id={facility_a}",
        json={
            "purpose": "messaging",
            "decision": "withdraw",
            "method": "documentary",
            "language": "en",
        },
        headers={"X-CSRF-Token": _csrf(client_b), "Idempotency-Key": str(uuid4())},
    )
    assert withdraw_response.status_code == 404, withdraw_response.json


def test_consent_grant_requires_evidence(client, identity):
    """Consent grant without a valid evidence reference must fail closed."""
    _login(client, "tst.vht.a")
    facility_id = str(identity.facility_ids["TST-A"])
    patient_id = _register_synthetic_patient(client, facility_id, "grant-evidence")

    notice_id, _evidence_id = _create_consent_fixtures(
        identity.app, facility_id, identity.user_ids["vht_a"], patient_id, "EVIDENCE"
    )

    # Grant without evidence_id should fail (422)
    response = client.post(
        f"/api/v1/patients/{patient_id}/consent-events?facility_id={facility_id}",
        json={
            "purpose": "messaging",
            "decision": "grant",
            "notice_version_id": str(notice_id),
            "method": "documentary",
            "language": "en",
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 422, response.json

    # Grant with non-existent evidence_id should fail (422)
    response = client.post(
        f"/api/v1/patients/{patient_id}/consent-events?facility_id={facility_id}",
        json={
            "purpose": "messaging",
            "decision": "grant",
            "notice_version_id": str(notice_id),
            "method": "documentary",
            "language": "en",
            "evidence_id": str(uuid4()),
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 422, response.json


def test_eligibility_idempotency_replay(client, identity):
    """Same idempotency key replays the existing eligibility check; different payload rejects."""
    _login(client, "tst.vht.a")
    facility_id = str(identity.facility_ids["TST-A"])
    patient_id = _register_synthetic_patient(client, facility_id, "elig-idem")

    key = str(uuid4())
    response1 = client.post(
        f"/api/v1/patients/{patient_id}/eligibility?facility_id={facility_id}",
        json={
            "method": "documentary",
            "result": "verified",
            "checked_at": "2026-01-15T10:00:00Z",
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": key},
    )
    assert response1.status_code == 201, response1.json
    seq1 = response1.json["data"]["sequence"]

    # Same key, same payload -> replay returns original
    response2 = client.post(
        f"/api/v1/patients/{patient_id}/eligibility?facility_id={facility_id}",
        json={
            "method": "documentary",
            "result": "verified",
            "checked_at": "2026-01-15T10:00:00Z",
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": key},
    )
    assert response2.status_code == 201, response2.json
    assert response2.json["data"]["sequence"] == seq1
    assert response2.json["data"]["result"] == "verified"

    # Same key, different payload -> conflict
    response3 = client.post(
        f"/api/v1/patients/{patient_id}/eligibility?facility_id={facility_id}",
        json={
            "method": "documentary",
            "result": "ineligible",
            "checked_at": "2026-01-15T10:00:00Z",
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": key},
    )
    assert response3.status_code == 409, response3.json


def test_consent_idempotency_replay(client, identity):
    """Same idempotency key replays the existing consent event; different payload rejects."""
    _login(client, "tst.vht.a")
    facility_id = str(identity.facility_ids["TST-A"])
    patient_id = _register_synthetic_patient(client, facility_id, "consent-idem")

    notice_id, evidence_id = _create_consent_fixtures(
        identity.app, facility_id, identity.user_ids["vht_a"], patient_id, "IDEMPOTENCY"
    )

    key = str(uuid4())
    response1 = client.post(
        f"/api/v1/patients/{patient_id}/consent-events?facility_id={facility_id}",
        json={
            "purpose": "messaging",
            "decision": "grant",
            "notice_version_id": str(notice_id),
            "method": "documentary",
            "language": "en",
            "evidence_id": str(evidence_id),
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": key},
    )
    assert response1.status_code == 201, response1.json
    seq1 = response1.json["data"]["event_sequence"]

    # Same key, same payload -> replay returns original
    response2 = client.post(
        f"/api/v1/patients/{patient_id}/consent-events?facility_id={facility_id}",
        json={
            "purpose": "messaging",
            "decision": "grant",
            "notice_version_id": str(notice_id),
            "method": "documentary",
            "language": "en",
            "evidence_id": str(evidence_id),
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": key},
    )
    assert response2.status_code == 201, response2.json
    assert response2.json["data"]["event_sequence"] == seq1
    assert response2.json["data"]["decision"] == "grant"

    # Same key, different payload -> conflict
    response3 = client.post(
        f"/api/v1/patients/{patient_id}/consent-events?facility_id={facility_id}",
        json={
            "purpose": "messaging",
            "decision": "withdraw",
            "method": "documentary",
            "language": "en",
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": key},
    )
    assert response3.status_code == 409, response3.json


def test_cross_patient_evidence_list_denied(client, identity):
    """Patient B's evidence list must not include patient A's evidence."""
    _login(client, "tst.vht.a")
    facility_id = str(identity.facility_ids["TST-A"])
    patient_a_id = _register_synthetic_patient(client, facility_id, "ev-list-a")
    patient_b_id = _register_synthetic_patient(client, facility_id, "ev-list-b")

    # Create consent fixtures and a grant event for patient A
    notice_id, evidence_id = _create_consent_fixtures(
        identity.app, facility_id, identity.user_ids["vht_a"], patient_a_id, "EV-LIST-A"
    )
    client.post(
        f"/api/v1/patients/{patient_a_id}/consent-events?facility_id={facility_id}",
        json={
            "purpose": "messaging",
            "decision": "grant",
            "notice_version_id": str(notice_id),
            "method": "documentary",
            "language": "en",
            "evidence_id": str(evidence_id),
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": str(uuid4())},
    )

    # Patient B's evidence list should be empty (no consent events for patient B)
    response = client.get(
        f"/api/v1/patients/{patient_b_id}/evidence?facility_id={facility_id}",
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert response.status_code == 200, response.json
    assert response.json["data"] == [], "Patient B should not see patient A's evidence"


def test_consent_idempotency_actor_mismatch_rejected(client, identity):
    """Same idempotency key used by an unauthorised actor must get 404 before idempotency check."""
    _login(client, "tst.vht.a")
    facility_id = str(identity.facility_ids["TST-A"])
    patient_id = _register_synthetic_patient(client, facility_id, "idem-actor")

    notice_id, evidence_id = _create_consent_fixtures(
        identity.app, facility_id, identity.user_ids["vht_a"], patient_id, "IDEM-ACTOR"
    )

    key = str(uuid4())
    # First request by vht.a (authorised for TST-A)
    response1 = client.post(
        f"/api/v1/patients/{patient_id}/consent-events?facility_id={facility_id}",
        json={
            "purpose": "messaging",
            "decision": "grant",
            "notice_version_id": str(notice_id),
            "method": "documentary",
            "language": "en",
            "evidence_id": str(evidence_id),
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": key},
    )
    assert response1.status_code == 201, response1.json

    # Second request by vht.b (NOT authorised for TST-A) -> 404 before idempotency check
    client_b = identity.app.test_client()
    _login(client_b, "tst.vht.b")
    response2 = client_b.post(
        f"/api/v1/patients/{patient_id}/consent-events?facility_id={facility_id}",
        json={
            "purpose": "messaging",
            "decision": "grant",
            "notice_version_id": str(notice_id),
            "method": "documentary",
            "language": "en",
            "evidence_id": str(evidence_id),
        },
        headers={"X-CSRF-Token": _csrf(client_b), "Idempotency-Key": key},
    )
    assert response2.status_code == 404, response2.json


def test_consent_idempotency_different_authorised_actor_rejected(client, identity):
    """Same idempotency key used by two separately authorised actors must be rejected with 409."""
    _login(client, "tst.vht.a")
    facility_id = str(identity.facility_ids["TST-A"])
    patient_id = _register_synthetic_patient(client, facility_id, "idem-diff-actor")

    notice_id, evidence_id = _create_consent_fixtures(
        identity.app, facility_id, identity.user_ids["vht_a"], patient_id, "IDEM-DIFF-ACTOR"
    )

    key = str(uuid4())
    # First request by vht.a (authorised for TST-A)
    response1 = client.post(
        f"/api/v1/patients/{patient_id}/consent-events?facility_id={facility_id}",
        json={
            "purpose": "messaging",
            "decision": "grant",
            "notice_version_id": str(notice_id),
            "method": "documentary",
            "language": "en",
            "evidence_id": str(evidence_id),
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": key},
    )
    assert response1.status_code == 201, response1.json

    # Give the existing TST-A VHT member an individual assignment to this patient.
    owner = create_engine(postgres_url(os.environ["TEST_MIGRATION_DATABASE_URL"]))
    try:
        with owner.begin() as connection:
            result = connection.execute(
                text(
                    "INSERT INTO participant_assignments "
                    "(id, episode_facility_assignment_id, facility_id, "
                    "membership_id, duty, valid_from) "
                    "SELECT :id, efa.id, efa.facility_id, fm.id, 'primary', now() "
                    "FROM episode_facility_assignments efa JOIN facility_memberships fm "
                    "ON fm.facility_id = efa.facility_id "
                    "WHERE efa.patient_id = :patient AND efa.facility_id = :facility "
                    "AND fm.user_id = :actor AND fm.status = 'active'"
                ),
                {
                    "id": uuid4(),
                    "patient": patient_id,
                    "facility": facility_id,
                    "actor": identity.user_ids["multi"],
                },
            )
            assert result.rowcount == 1
    finally:
        owner.dispose()
    client_b = identity.app.test_client()
    _login(client_b, "tst.multi")
    response2 = client_b.post(
        f"/api/v1/patients/{patient_id}/consent-events?facility_id={facility_id}",
        json={
            "purpose": "messaging",
            "decision": "grant",
            "notice_version_id": str(notice_id),
            "method": "documentary",
            "language": "en",
            "evidence_id": str(evidence_id),
        },
        headers={"X-CSRF-Token": _csrf(client_b), "Idempotency-Key": key},
    )
    assert response2.status_code == 409, response2.json


def test_evidence_list_unauthorized_patient_denied(client, identity):
    """Evidence list for a patient the user is not assigned to must be denied."""
    _login(client, "tst.vht.a")
    facility_id = str(identity.facility_ids["TST-A"])
    patient_id = _register_synthetic_patient(client, facility_id, "ev-unauth")

    # vht.b is not assigned to any patient at TST-A
    client_b = identity.app.test_client()
    _login(client_b, "tst.vht.b")

    response = client_b.get(
        f"/api/v1/patients/{patient_id}/evidence?facility_id={facility_id}",
        headers={"X-CSRF-Token": _csrf(client_b)},
    )
    assert response.status_code == 404, response.json


def test_evidence_registration_for_patient(client, identity):
    """Register synthetic evidence for a patient via POST endpoint."""
    _login(client, "tst.vht.a")
    facility_id = str(identity.facility_ids["TST-A"])
    patient_id = _register_synthetic_patient(client, facility_id, "ev-reg")

    response = client.post(
        f"/api/v1/patients/{patient_id}/evidence?facility_id={facility_id}",
        json={
            "storage_reference": "synthetic-ref-001",
            "digest": "a" * 64,
            "media_type": "application/pdf",
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 201, response.json
    assert response.json["data"]["media_type"] == "application/pdf"
    assert response.json["data"]["id"] is not None

    # Re-register same evidence -> returns existing (idempotent)
    response2 = client.post(
        f"/api/v1/patients/{patient_id}/evidence?facility_id={facility_id}",
        json={
            "storage_reference": "synthetic-ref-001",
            "digest": "a" * 64,
            "media_type": "application/pdf",
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": str(uuid4())},
    )
    assert response2.status_code == 201, response2.json
    assert response2.json["data"]["id"] == response.json["data"]["id"]


def test_cross_patient_evidence_cannot_be_used_for_grant(client, identity):
    """Patient A's evidence cannot be used for Patient B's consent grant (same facility)."""
    _login(client, "tst.vht.a")
    facility_id = str(identity.facility_ids["TST-A"])
    patient_a_id = _register_synthetic_patient(client, facility_id, "cross-grant-a")
    patient_b_id = _register_synthetic_patient(client, facility_id, "cross-grant-b")

    # Create evidence associated with Patient A
    notice_id, evidence_id = _create_consent_fixtures(
        identity.app, facility_id, identity.user_ids["vht_a"], patient_a_id, "CROSS-GRANT"
    )

    # Patient B tries to grant using Patient A's evidence -> 422
    response = client.post(
        f"/api/v1/patients/{patient_b_id}/consent-events?facility_id={facility_id}",
        json={
            "purpose": "messaging",
            "decision": "grant",
            "notice_version_id": str(notice_id),
            "method": "documentary",
            "language": "en",
            "evidence_id": str(evidence_id),
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 422, response.json


def test_patient_evidence_isolation_same_facility(client, identity):
    """Patient A's evidence appears only for Patient A, not Patient B (same facility)."""
    _login(client, "tst.vht.a")
    facility_id = str(identity.facility_ids["TST-A"])
    patient_a_id = _register_synthetic_patient(client, facility_id, "ev-iso-a")
    patient_b_id = _register_synthetic_patient(client, facility_id, "ev-iso-b")

    # Register evidence for Patient A
    notice_id, evidence_id = _create_consent_fixtures(
        identity.app, facility_id, identity.user_ids["vht_a"], patient_a_id, "EV-ISO-A"
    )

    # Patient A's evidence list includes the evidence
    response_a = client.get(
        f"/api/v1/patients/{patient_a_id}/evidence?facility_id={facility_id}",
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert response_a.status_code == 200, response_a.json
    assert len(response_a.json["data"]) >= 1
    assert any(e["id"] == str(evidence_id) for e in response_a.json["data"])

    # Patient B's evidence list is empty
    response_b = client.get(
        f"/api/v1/patients/{patient_b_id}/evidence?facility_id={facility_id}",
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert response_b.status_code == 200, response_b.json
    assert response_b.json["data"] == [], "Patient B should not see Patient A's evidence"


def test_consent_grant_withdraw_full_flow(client, identity):
    """Full consent grant → withdrawal → history flow with patient-specific evidence."""
    _login(client, "tst.vht.a")
    facility_id = str(identity.facility_ids["TST-A"])
    patient_id = _register_synthetic_patient(client, facility_id, "full-flow")

    # Register evidence for the patient
    ev_response = client.post(
        f"/api/v1/patients/{patient_id}/evidence?facility_id={facility_id}",
        json={
            "storage_reference": "synthetic-ref-full-flow",
            "digest": "b" * 64,
            "media_type": "application/pdf",
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": str(uuid4())},
    )
    assert ev_response.status_code == 201, ev_response.json
    evidence_id = ev_response.json["data"]["id"]

    # Notice publication uses the separately privileged fixture connection.
    notice_id, _ = _create_consent_fixtures(
        identity.app, facility_id, identity.user_ids["vht_a"], patient_id, "FULL-FLOW"
    )

    # Grant consent
    grant_response = client.post(
        f"/api/v1/patients/{patient_id}/consent-events?facility_id={facility_id}",
        json={
            "purpose": "messaging",
            "decision": "grant",
            "notice_version_id": str(notice_id),
            "method": "documentary",
            "language": "en",
            "evidence_id": str(evidence_id),
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": str(uuid4())},
    )
    assert grant_response.status_code == 201, grant_response.json
    assert grant_response.json["data"]["decision"] == "grant"
    assert grant_response.json["data"]["evidence_id"] == str(evidence_id)

    # Withdraw consent
    withdraw_response = client.post(
        f"/api/v1/patients/{patient_id}/consent-events?facility_id={facility_id}",
        json={
            "purpose": "messaging",
            "decision": "withdraw",
            "method": "documentary",
            "language": "en",
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": str(uuid4())},
    )
    assert withdraw_response.status_code == 201, withdraw_response.json
    assert withdraw_response.json["data"]["decision"] == "withdraw"

    # Verify history
    events_response = client.get(
        f"/api/v1/patients/{patient_id}/consent-events?facility_id={facility_id}",
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert events_response.status_code == 200
    events = events_response.json["data"]
    decisions = {event["decision"] for event in events}
    assert "grant" in decisions
    assert "withdraw" in decisions


def test_rls_cross_patient_direct_query_denied(client, identity):
    """Direct SQL cross-patient query must be denied by RLS even with facility context."""
    _login(client, "tst.vht.a")
    facility_id = str(identity.facility_ids["TST-A"])
    patient_a_id = _register_synthetic_patient(client, facility_id, "rls-cross-a")
    patient_b_id = _register_synthetic_patient(client, facility_id, "rls-cross-b")

    # Register evidence for patient A via API
    ev_response = client.post(
        f"/api/v1/patients/{patient_a_id}/evidence?facility_id={facility_id}",
        json={
            "storage_reference": "rls-ref-a",
            "digest": "c" * 64,
            "media_type": "application/pdf",
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": str(uuid4())},
    )
    assert ev_response.status_code == 201

    # Query as patient B's context - should return 0 rows due to RLS
    with identity.app.app_context():
        from tunza.access import load_access_context
        from tunza.identity.models import User
        from tunza.patients.security import establish_patient_context

        user_b = db.session.get(User, identity.user_ids["vht_a"])
        context_b = load_access_context(user_b)
        # Set context for patient B's facility
        establish_patient_context(context_b, facility_id)
        # Override patient_id to patient B's ID to simulate cross-patient access
        db.session.execute(
            sa.text("SELECT set_config('tunza.patient_id', :pid, true)"),
            {"pid": str(patient_b_id)},
        )
        count = db.session.scalar(
            sa.text("SELECT count(*) FROM protected_evidence WHERE patient_id = :pid"),
            {"pid": str(patient_a_id)},
        )
        assert count == 0, "RLS should prevent cross-patient evidence access"


def test_rls_missing_patient_context_denied(client, identity):
    """Query without tunza.patient_id context must return no rows (fail closed)."""
    _login(client, "tst.vht.a")
    facility_id = str(identity.facility_ids["TST-A"])
    patient_id = _register_synthetic_patient(client, facility_id, "rls-noctx")

    ev_response = client.post(
        f"/api/v1/patients/{patient_id}/evidence?facility_id={facility_id}",
        json={
            "storage_reference": "rls-ref-noctx",
            "digest": "d" * 64,
            "media_type": "application/pdf",
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": str(uuid4())},
    )
    assert ev_response.status_code == 201

    # Query without setting patient_id context - RLS should block
    with identity.app.app_context():
        # Don't set tunza.patient_id - only facility_id
        db.session.execute(
            sa.text("SELECT set_config('tunza.facility_id', :fid, true)"),
            {"fid": str(facility_id)},
        )
        count = db.session.scalar(sa.text("SELECT count(*) FROM protected_evidence"))
        assert count == 0, "RLS should deny access without patient context"


def test_rls_pooled_connection_no_context_leak(client, identity):
    """RLS context must not leak across pooled connections."""
    _login(client, "tst.vht.a")
    facility_id = str(identity.facility_ids["TST-A"])
    patient_a_id = _register_synthetic_patient(client, facility_id, "rls-pool-a")
    _register_synthetic_patient(client, facility_id, "rls-pool-b")

    # Register evidence for patient A
    ev_response = client.post(
        f"/api/v1/patients/{patient_a_id}/evidence?facility_id={facility_id}",
        json={
            "storage_reference": "rls-pool-ref",
            "digest": "e" * 64,
            "media_type": "application/pdf",
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": str(uuid4())},
    )
    assert ev_response.status_code == 201

    # Use a separate connection (simulating pooled connection) without patient context
    runtime_url = postgres_url(os.environ["TEST_DATABASE_URL"])
    runtime = create_engine(runtime_url, pool_size=1, max_overflow=0)
    try:
        with runtime.connect() as connection:
            # Only set facility_id, NOT patient_id
            connection.execute(
                sa.text("SELECT set_config('tunza.facility_id', :fid, true)"),
                {"fid": str(facility_id)},
            )
            count = connection.execute(
                sa.text("SELECT count(*) FROM protected_evidence")
            ).scalar_one()
            assert count == 0, "Pooled connection without patient context must see no rows"
    finally:
        runtime.dispose()


def test_consent_idempotency_concurrent_same_key(client, identity):
    """Concurrent requests with same idempotency key must produce exactly one event."""
    _login(client, "tst.vht.a")
    facility_id = str(identity.facility_ids["TST-A"])
    patient_id = _register_synthetic_patient(client, facility_id, "idem-concurrent")

    notice_id, evidence_id = _create_consent_fixtures(
        identity.app, facility_id, identity.user_ids["vht_a"], patient_id, "IDEM-CONC"
    )

    key = str(uuid4())
    csrf_a = _csrf(client)
    client_b = identity.app.test_client()
    _login(client_b, "tst.vht.a")
    csrf_b = _csrf(client_b)

    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)
    results = {}

    def submit(client, csrf, label):
        barrier.wait(timeout=10)
        response = client.post(
            f"/api/v1/patients/{patient_id}/consent-events?facility_id={facility_id}",
            json={
                "purpose": "messaging",
                "decision": "grant",
                "notice_version_id": str(notice_id),
                "method": "documentary",
                "language": "en",
                "evidence_id": str(evidence_id),
            },
            headers={"X-CSRF-Token": csrf, "Idempotency-Key": key},
        )
        results[label] = (response.status_code, response.json)

    with ThreadPoolExecutor(max_workers=2) as executor:
        executor.submit(submit, client, csrf_a, "a")
        executor.submit(submit, client_b, csrf_b, "b")

    statuses = {results[k][0] for k in results}
    assert statuses == {201}, results
    # Both should return the same event (idempotent replay)
    event_ids = {results[k][1]["data"]["id"] for k in results}
    assert len(event_ids) == 1, f"Expected single event, got {event_ids}"


def test_consent_audit_failure_rolls_back_full(client, identity, monkeypatch):
    """Audit failure during consent grant must roll back the entire transaction."""
    import tunza.patients.routes as patient_routes

    _login(client, "tst.vht.a")
    facility_id = str(identity.facility_ids["TST-A"])
    patient_id = _register_synthetic_patient(client, facility_id, "audit-consent")

    notice_id, evidence_id = _create_consent_fixtures(
        identity.app, facility_id, identity.user_ids["vht_a"], patient_id, "AUDIT-CONSENT"
    )

    def fail_audit(**_kwargs):
        raise SQLAlchemyError("synthetic audit failure")

    with monkeypatch.context() as patch:
        patch.setattr(patient_routes, "_record_patient_audit", fail_audit)
        response = client.post(
            f"/api/v1/patients/{patient_id}/consent-events?facility_id={facility_id}",
            json={
                "purpose": "messaging",
                "decision": "grant",
                "notice_version_id": str(notice_id),
                "method": "documentary",
                "language": "en",
                "evidence_id": str(evidence_id),
            },
            headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": str(uuid4())},
        )
        assert response.status_code == 503, response.json

    # Verify no consent event was created despite the 503
    events_response = client.get(
        f"/api/v1/patients/{patient_id}/consent-events?facility_id={facility_id}",
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert events_response.status_code == 200
    assert events_response.json["data"] == [], "No event should exist after audit failure rollback"

    # Retry with working audit - should succeed
    retry_response = client.post(
        f"/api/v1/patients/{patient_id}/consent-events?facility_id={facility_id}",
        json={
            "purpose": "messaging",
            "decision": "grant",
            "notice_version_id": str(notice_id),
            "method": "documentary",
            "language": "en",
            "evidence_id": str(evidence_id),
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": str(uuid4())},
    )
    assert retry_response.status_code == 201, retry_response.json
