"""PostgreSQL integration tests for controlled staff and facility administration."""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import inspect, select

from tunza import create_app
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

PASSWORD = "synthetic-admin-password"
NEW_PASSWORD = "activated-synthetic-password"


@pytest.fixture(scope="module")
def admin_app():
    import os

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

    application = create_app(
        {
            "APP_ENV": "testing",
            "SECRET_KEY": secrets.token_urlsafe(48),
            "DATABASE_URL": value,
            "MIGRATION_DATABASE_URL": value,
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
def admin_seed(admin_app):
    with admin_app.app_context():
        roles = {role.code: role for role in db.session.scalars(select(Role)).all()}
        for code in ("VHT", "HCW", "FA", "AUTHOR", "PA"):
            assert code in roles, f"missing seeded role {code}"
        assert (
            db.session.scalar(select(Permission).where(Permission.code == "account.provision"))
            is not None
        )

        facilities: dict[str, Facility] = {}
        for code, name in (
            ("ADM-A", "Admin Clinic A"),
            ("ADM-B", "Admin Clinic B"),
        ):
            facility = Facility(code=code, name=name, timezone="Africa/Kampala", active=True)
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
            "pa": make_user("adm.pa", "Admin Platform Admin"),
            "fa_a": make_user("adm.fa.a", "Admin FA A"),
            "vht_a": make_user("adm.vht.a", "Admin VHT A"),
            "vht_b": make_user("adm.vht.b", "Admin VHT B"),
            "author": make_user("adm.author", "Admin Author"),
            "target": make_user("adm.target", "Admin Target"),
            "already": make_user("adm.already", "Admin Already Member"),
        }

        now = datetime.now(UTC)

        def bind(user: User, role_code: str, scope_kind: str, facility: Facility | None = None):
            binding = RoleBinding(
                user_id=user.id,
                role_id=roles[role_code].id,
                facility_id=facility.id if facility is not None else None,
                scope_kind=scope_kind,
                starts_at=now,
                ends_at=None,
                granted_by=users["pa"].id,
            )
            db.session.add(binding)
            db.session.flush()
            return binding

        def member(user: User, facility: Facility) -> FacilityMembership:
            membership = FacilityMembership(
                user_id=user.id,
                facility_id=facility.id,
                status="active",
                starts_at=now,
                ends_at=None,
            )
            db.session.add(membership)
            db.session.flush()
            return membership

        bind(users["pa"], "PA", "platform")
        bind(users["fa_a"], "FA", "facility", facilities["ADM-A"])
        member(users["fa_a"], facilities["ADM-A"])
        bind(users["vht_a"], "VHT", "facility", facilities["ADM-A"])
        member(users["vht_a"], facilities["ADM-A"])
        bind(users["vht_b"], "VHT", "facility", facilities["ADM-B"])
        member(users["vht_b"], facilities["ADM-B"])
        bind(users["author"], "AUTHOR", "programme")
        bind(users["target"], "VHT", "facility", facilities["ADM-A"])
        member(users["target"], facilities["ADM-A"])
        member(users["already"], facilities["ADM-A"])

        session = AuthSession(
            user_id=users["vht_a"].id,
            token_hash=hash_session_token("pre-disable-session-token"),
            state="active",
            expires_at=now + timedelta(hours=1),
            last_seen_at=now,
            security_version=users["vht_a"].security_version,
        )
        db.session.add(session)

        snapshot = SimpleNamespace(
            app=admin_app,
            facility_ids={code: facility.id for code, facility in facilities.items()},
            user_ids={name: user.id for name, user in users.items()},
            password=PASSWORD,
            new_password=NEW_PASSWORD,
        )
        db.session.commit()
        return snapshot


@pytest.fixture(autouse=True)
def _fresh_rate_limiter(admin_app):
    limiter = admin_app.extensions["tunza.login_rate_limiter"]
    limiter.clear()
    yield
    limiter.clear()


@pytest.fixture
def client(admin_app):
    return admin_app.test_client()


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


def _patch(client, path: str, body: dict, *, expect: int = 200):
    response = client.patch(path, json=body, headers={"X-CSRF-Token": _csrf(client)})
    assert response.status_code == expect, response.json
    return response


def _post(client, path: str, body: dict, *, expect: int = 200):
    response = client.post(path, json=body, headers={"X-CSRF-Token": _csrf(client)})
    assert response.status_code == expect, response.json
    return response


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def test_vht_cannot_use_admin_endpoints(client, admin_seed):
    _login(client, "adm.vht.a")
    assert client.get("/api/v1/users").status_code == 403
    assert client.get("/api/v1/memberships").status_code == 403
    assert client.get("/api/v1/role-bindings").status_code == 403


def test_pa_provisions_account_activates_and_grants_role(client, admin_seed):
    _login(client, "adm.pa")
    created = _post(
        client,
        "/api/v1/users",
        {
            "login_identifier": "adm.new.staff",
            "display_name": "Admin New Staff",
            "facility_id": admin_seed.facility_ids["ADM-A"],
        },
        expect=201,
    )
    account = created.json["data"]["account"]
    token = created.json["data"]["activation_token"]
    assert account["status"] == "invited"
    assert account["login_identifier"] == "adm.new.staff"
    assert "activation_token" not in account

    with admin_seed.app.app_context():
        user = db.session.scalar(select(User).where(User.login_identifier == "adm.new.staff"))
        assert user is not None
        assert user.status == "invited"
        assert user.activation_token_hash is not None
        assert token.encode() not in (user.activation_token_hash or b"")

    activated = client.post(
        "/api/v1/auth/activations",
        json={"token": token, "new_password": admin_seed.new_password},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert activated.status_code == 200, activated.json
    assert activated.json["data"]["state"] == "activated"

    again = client.post(
        "/api/v1/auth/activations",
        json={"token": token, "new_password": admin_seed.new_password},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert again.status_code == 400

    # New staff can sign in with the activated password but has no admin permissions yet.
    client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": _csrf(client)})
    _login(client, "adm.new.staff", admin_seed.new_password)
    assert client.get("/api/v1/memberships").status_code == 403
    client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": _csrf(client)})
    _login(client, "adm.pa")

    member = _post(
        client,
        "/api/v1/memberships",
        {
            "user_id": created.json["data"]["account"]["id"],
            "facility_id": admin_seed.facility_ids["ADM-A"],
            "starts_at": _iso_now(),
        },
        expect=201,
    )
    assert member.json["data"]["status"] == "active"

    # PA cannot grant clinical care roles; an FA at the facility may grant VHT.
    denied = client.post(
        "/api/v1/role-bindings",
        json={
            "user_id": created.json["data"]["account"]["id"],
            "role_code": "VHT",
            "scope_kind": "facility",
            "facility_id": admin_seed.facility_ids["ADM-A"],
            "starts_at": _iso_now(),
        },
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert denied.status_code == 403

    client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": _csrf(client)})
    _login(client, "adm.fa.a")
    binding = _post(
        client,
        "/api/v1/role-bindings",
        {
            "user_id": created.json["data"]["account"]["id"],
            "role_code": "VHT",
            "scope_kind": "facility",
            "facility_id": admin_seed.facility_ids["ADM-A"],
            "starts_at": _iso_now(),
        },
        expect=201,
    )
    assert binding.json["data"]["role_code"] == "VHT"
    binding_id = binding.json["data"]["id"]

    revoked = _post(
        client,
        f"/api/v1/role-bindings/{binding_id}/revoke",
        {"reason": "synthetic revoke"},
        expect=200,
    )
    assert revoked.json["data"]["ends_at"] is not None

    with admin_seed.app.app_context():
        events = list(
            db.session.scalars(
                select(AuditEvent).where(
                    AuditEvent.action.in_(["account.provision", "role.delegate", "role.revoke"]),
                    AuditEvent.outcome == "success",
                )
            ).all()
        )
        assert len(events) >= 3
        for event in events:
            assert admin_seed.password not in json.dumps(event.event_metadata)
            assert token not in json.dumps(event.event_metadata)


def test_fa_cannot_provision_or_manage_outside_facility(client, admin_seed):
    _login(client, "adm.fa.a")
    cross = _post(
        client,
        "/api/v1/users",
        {
            "login_identifier": "adm.cross.facility",
            "display_name": "Cross Facility",
            "facility_id": admin_seed.facility_ids["ADM-B"],
        },
        expect=404,
    )
    assert cross.status_code == 404

    ok = _post(
        client,
        "/api/v1/users",
        {
            "login_identifier": "adm.fa.created",
            "display_name": "FA Created",
            "facility_id": admin_seed.facility_ids["ADM-A"],
        },
        expect=201,
    )
    assert ok.status_code == 201
    created_id = ok.json["data"]["account"]["id"]

    # Invited accounts appear in the FA listing only after an active membership exists.
    _post(
        client,
        "/api/v1/memberships",
        {
            "user_id": created_id,
            "facility_id": admin_seed.facility_ids["ADM-A"],
            "starts_at": _iso_now(),
        },
        expect=201,
    )

    listing = client.get("/api/v1/users")
    assert listing.status_code == 200
    logins = {item["login_identifier"] for item in listing.json["data"]}
    assert "adm.fa.created" in logins
    assert "adm.vht.b" not in logins

    cross_membership = _post(
        client,
        "/api/v1/memberships",
        {
            "user_id": admin_seed.user_ids["author"],
            "facility_id": admin_seed.facility_ids["ADM-B"],
            "starts_at": _iso_now(),
        },
        expect=404,
    )
    assert cross_membership.status_code == 404


def test_pa_cannot_grant_clinical_roles(client, admin_seed):
    _login(client, "adm.pa")
    for role_code in ("VHT", "HCW", "REVIEWER"):
        response = client.post(
            "/api/v1/role-bindings",
            json={
                "user_id": admin_seed.user_ids["already"],
                "role_code": role_code,
                "scope_kind": "facility" if role_code != "REVIEWER" else "programme",
                **(
                    {"facility_id": admin_seed.facility_ids["ADM-A"]}
                    if role_code != "REVIEWER"
                    else {}
                ),
                "starts_at": _iso_now(),
            },
            headers={"X-CSRF-Token": _csrf(client)},
        )
        assert response.status_code == 403, (role_code, response.json)

    allowed = _post(
        client,
        "/api/v1/role-bindings",
        {
            "user_id": admin_seed.user_ids["already"],
            "role_code": "FA",
            "scope_kind": "facility",
            "facility_id": admin_seed.facility_ids["ADM-A"],
            "starts_at": _iso_now(),
        },
        expect=201,
    )
    assert allowed.json["data"]["role_code"] == "FA"


def test_self_grant_is_forbidden(client, admin_seed):
    _login(client, "adm.fa.a")
    response = client.post(
        "/api/v1/role-bindings",
        json={
            "user_id": admin_seed.user_ids["fa_a"],
            "role_code": "VHT",
            "scope_kind": "facility",
            "facility_id": admin_seed.facility_ids["ADM-A"],
            "starts_at": _iso_now(),
        },
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert response.status_code == 403
    assert response.json["error"]["code"] == "forbidden"


def test_fa_cannot_delegate_across_facility(client, admin_seed):
    _login(client, "adm.fa.a")
    response = client.post(
        "/api/v1/role-bindings",
        json={
            "user_id": admin_seed.user_ids["target"],
            "role_code": "VHT",
            "scope_kind": "facility",
            "facility_id": admin_seed.facility_ids["ADM-B"],
            "starts_at": _iso_now(),
        },
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert response.status_code == 404


def test_fa_cannot_assign_care_role_without_membership(client, admin_seed):
    _login(client, "adm.fa.a")
    response = client.post(
        "/api/v1/role-bindings",
        json={
            "user_id": admin_seed.user_ids["author"],
            "role_code": "VHT",
            "scope_kind": "facility",
            "facility_id": admin_seed.facility_ids["ADM-A"],
            "starts_at": _iso_now(),
        },
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert response.status_code == 409


def test_membership_end_and_account_disable_revoke_access(client, admin_seed):
    _login(client, "adm.pa")
    memberships = client.get("/api/v1/memberships").json["data"]
    target_membership = next(
        item
        for item in memberships
        if item["user_id"] == str(admin_seed.user_ids["target"])
        and item["facility_id"] == str(admin_seed.facility_ids["ADM-A"])
    )
    ended = _patch(
        client,
        f"/api/v1/memberships/{target_membership['id']}",
        {"status": "ended", "reason": "synthetic end"},
        expect=200,
    )
    assert ended.json["data"]["status"] == "ended"
    assert ended.json["data"]["ends_at"] is not None

    with admin_seed.app.app_context():
        membership = db.session.get(FacilityMembership, target_membership["id"])
        assert membership is not None
        assert membership.status == "ended"

    # Capture a live VHT session, then have PA disable the account out-of-band.
    client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": _csrf(client)})
    _login(client, "adm.vht.a")
    session_token = client.get_cookie("tunza_session").value
    assert client.get("/api/v1/auth/me").status_code == 200

    client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": _csrf(client)})
    _login(client, "adm.pa")
    disabled = _patch(
        client,
        f"/api/v1/users/{admin_seed.user_ids['vht_a']}",
        {"status": "disabled", "reason": "synthetic disable"},
        expect=200,
    )
    assert disabled.json["data"]["status"] == "disabled"

    client.set_cookie("tunza_session", session_token, samesite="Lax")
    assert client.get("/api/v1/auth/me").status_code == 401

    with admin_seed.app.app_context():
        record = db.session.scalar(
            select(AuthSession).where(AuthSession.token_hash == hash_session_token(session_token))
        )
        assert record is not None
        assert record.state == "revoked"
        user = db.session.get(User, admin_seed.user_ids["vht_a"])
        assert user is not None
        assert user.security_version >= 2

    response = client.patch(
        f"/api/v1/users/{admin_seed.user_ids['pa']}",
        json={"status": "disabled", "reason": "self"},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert response.status_code in (401, 403)


def test_admin_cannot_disable_self_or_unknown_fields(client, admin_seed):
    _login(client, "adm.pa")
    response = client.patch(
        f"/api/v1/users/{admin_seed.user_ids['pa']}",
        json={"status": "disabled", "reason": "nope"},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert response.status_code == 403

    bad = client.post(
        "/api/v1/users",
        json={"login_identifier": "adm.x", "display_name": "X", "extra": True},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert bad.status_code == 422


def test_csrf_required_for_admin_mutations(client, admin_seed):
    _login(client, "adm.pa")
    response = client.post(
        "/api/v1/users",
        json={"login_identifier": "adm.csrf", "display_name": "CSF"},
    )
    assert response.status_code == 403


def test_duplicate_login_and_invalid_activation_are_generic(client, admin_seed):
    _login(client, "adm.pa")
    duplicate = client.post(
        "/api/v1/users",
        json={
            "login_identifier": "adm.vht.a",
            "display_name": "Dup",
            "facility_id": admin_seed.facility_ids["ADM-A"],
        },
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert duplicate.status_code == 409

    invalid = client.post(
        "/api/v1/auth/activations",
        json={"token": "not-a-real-token", "new_password": "synthetic-activation-password"},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert invalid.status_code == 400
    assert invalid.json["error"]["code"] == "invalid_token"


def test_revoking_future_dated_binding_never_activates(client, admin_seed):
    """A revoked future grant must never become active; ends_at stays > starts_at."""
    _login(client, "adm.fa.a")
    future_start = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    created = _post(
        client,
        "/api/v1/role-bindings",
        {
            "user_id": admin_seed.user_ids["already"],
            "role_code": "VHT",
            "scope_kind": "facility",
            "facility_id": admin_seed.facility_ids["ADM-A"],
            "starts_at": future_start,
        },
        expect=201,
    )
    binding_id = created.json["data"]["id"]
    assert created.json["data"]["ends_at"] is None

    revoked = _post(
        client,
        f"/api/v1/role-bindings/{binding_id}/revoke",
        {"reason": "synthetic future revoke"},
        expect=200,
    )
    ends_at_raw = revoked.json["data"]["ends_at"]
    starts_at_raw = revoked.json["data"]["starts_at"]
    assert ends_at_raw is not None
    ends_at = datetime.fromisoformat(ends_at_raw)
    starts_at = datetime.fromisoformat(starts_at_raw)
    now = datetime.now(UTC)
    # Closed at revoke time so starts_at <= now < ends_at can never hold later.
    assert ends_at <= now
    assert ends_at > starts_at
    assert starts_at < now

    with admin_seed.app.app_context():
        binding = db.session.get(RoleBinding, UUID(binding_id))
        assert binding is not None
        assert binding.ends_at is not None
        assert binding.ends_at > binding.starts_at
        # Access evaluation must not treat the binding as active.
        from tunza.access import load_access_context

        user = db.session.get(User, admin_seed.user_ids["already"])
        context = load_access_context(user)
        assert binding_id not in {str(item.id) for item in context.bindings}

    again = _post(
        client,
        f"/api/v1/role-bindings/{binding_id}/revoke",
        {"reason": "already revoked"},
        expect=409,
    )
    assert again.status_code == 409


def test_fa_cannot_disable_shared_or_global_accounts(client, admin_seed):
    """Facility admins cannot disable multi-facility or platform-scoped staff."""
    with admin_seed.app.app_context():
        roles = {role.code: role for role in db.session.scalars(select(Role)).all()}
        now = datetime.now(UTC)
        # Shared staff: active membership at both ADM-A and ADM-B.
        shared = User(
            login_identifier="adm.shared.staff",
            display_name="Admin Shared Staff",
            password_hash=hash_password(PASSWORD),
            status="active",
        )
        db.session.add(shared)
        db.session.flush()
        for code in ("ADM-A", "ADM-B"):
            db.session.add(
                FacilityMembership(
                    user_id=shared.id,
                    facility_id=admin_seed.facility_ids[code],
                    status="active",
                    starts_at=now,
                    ends_at=None,
                )
            )
        # Global staff: platform-scoped PA binding with membership only at ADM-A.
        global_staff = User(
            login_identifier="adm.global.staff",
            display_name="Admin Global Staff",
            password_hash=hash_password(PASSWORD),
            status="active",
        )
        db.session.add(global_staff)
        db.session.flush()
        db.session.add(
            FacilityMembership(
                user_id=global_staff.id,
                facility_id=admin_seed.facility_ids["ADM-A"],
                status="active",
                starts_at=now,
                ends_at=None,
            )
        )
        db.session.add(
            RoleBinding(
                user_id=global_staff.id,
                role_id=roles["PA"].id,
                facility_id=None,
                scope_kind="platform",
                starts_at=now,
                ends_at=None,
                granted_by=admin_seed.user_ids["pa"],
            )
        )
        db.session.commit()
        shared_id = str(shared.id)
        global_id = str(global_staff.id)

    _login(client, "adm.fa.a")

    shared_listing = client.get("/api/v1/users")
    assert shared_listing.status_code == 200
    logins = {item["login_identifier"] for item in shared_listing.json["data"]}
    assert "adm.shared.staff" in logins

    denied_shared = client.patch(
        f"/api/v1/users/{shared_id}",
        json={"status": "disabled", "reason": "synthetic shared disable"},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert denied_shared.status_code == 403
    assert denied_shared.json["error"]["code"] == "forbidden"

    denied_global = client.patch(
        f"/api/v1/users/{global_id}",
        json={"status": "disabled", "reason": "synthetic global disable"},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert denied_global.status_code == 403

    with admin_seed.app.app_context():
        shared = db.session.get(User, UUID(shared_id))
        global_staff = db.session.get(User, UUID(global_id))
        assert shared is not None and shared.status == "active"
        assert global_staff is not None and global_staff.status == "active"
        denied_events = list(
            db.session.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "account.state",
                    AuditEvent.outcome == "denied",
                )
            ).all()
        )
        assert any(
            event.event_metadata.get("reason") == "shared_or_global_account"
            for event in denied_events
        )

    # Platform administrator may still disable the shared account.
    client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": _csrf(client)})
    _login(client, "adm.pa")
    allowed = _patch(
        client,
        f"/api/v1/users/{shared_id}",
        {"status": "disabled", "reason": "synthetic pa disable"},
        expect=200,
    )
    assert allowed.json["data"]["status"] == "disabled"

    # Out-of-scope-only targets remain 404 for the facility admin.
    client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": _csrf(client)})
    _login(client, "adm.fa.a")
    missing = client.patch(
        f"/api/v1/users/{admin_seed.user_ids['vht_b']}",
        json={"status": "disabled", "reason": "cross facility"},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert missing.status_code == 404


def test_role_revocation_applies_delegation_allowlists(client, admin_seed):
    """Revoke uses the same allowlists as grant for clinical and platform roles."""
    with admin_seed.app.app_context():
        roles = {role.code: role for role in db.session.scalars(select(Role)).all()}
        now = datetime.now(UTC)
        # PA-owned clinical binding that PA must not be allowed to revoke.
        clinical_target = db.session.get(User, admin_seed.user_ids["vht_a"])
        clinical_binding = RoleBinding(
            user_id=clinical_target.id,
            role_id=roles["VHT"].id,
            facility_id=admin_seed.facility_ids["ADM-A"],
            scope_kind="facility",
            starts_at=now,
            ends_at=None,
            granted_by=admin_seed.user_ids["pa"],
        )
        # Programme binding that only platform-scope holders may revoke.
        programme_binding = RoleBinding(
            user_id=admin_seed.user_ids["author"],
            role_id=roles["AUTHOR"].id,
            facility_id=None,
            scope_kind="programme",
            starts_at=now,
            ends_at=None,
            granted_by=admin_seed.user_ids["pa"],
        )
        db.session.add_all([clinical_binding, programme_binding])
        db.session.commit()
        clinical_id = str(clinical_binding.id)
        programme_id = str(programme_binding.id)

    # PA cannot revoke protected clinical role VHT (same ban as grant).
    _login(client, "adm.pa")
    pa_denied = client.post(
        f"/api/v1/role-bindings/{clinical_id}/revoke",
        json={"reason": "synthetic pa clinical revoke"},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert pa_denied.status_code == 403
    assert pa_denied.json["error"]["code"] == "forbidden"

    # FA lacks platform scope for programme bindings → 404, not silent success.
    client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": _csrf(client)})
    _login(client, "adm.fa.a")
    fa_denied = client.post(
        f"/api/v1/role-bindings/{programme_id}/revoke",
        json={"reason": "synthetic fa programme revoke"},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert fa_denied.status_code == 404

    # FA may revoke an in-scope facility care role at their own facility.
    with admin_seed.app.app_context():
        roles = {role.code: role for role in db.session.scalars(select(Role)).all()}
        now = datetime.now(UTC)
        revokeable = RoleBinding(
            user_id=admin_seed.user_ids["already"],
            role_id=roles["VHT"].id,
            facility_id=admin_seed.facility_ids["ADM-A"],
            scope_kind="facility",
            starts_at=now,
            ends_at=None,
            granted_by=admin_seed.user_ids["fa_a"],
        )
        db.session.add(revokeable)
        db.session.commit()
        revokeable_id = str(revokeable.id)

    fa_ok = _post(
        client,
        f"/api/v1/role-bindings/{revokeable_id}/revoke",
        {"reason": "synthetic fa care revoke"},
        expect=200,
    )
    assert fa_ok.json["data"]["ends_at"] is not None

    # PA may revoke platform-scope roles inside the PA allowlist (e.g. another PA).
    client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": _csrf(client)})
    _login(client, "adm.pa")
    with admin_seed.app.app_context():
        roles = {role.code: role for role in db.session.scalars(select(Role)).all()}
        now = datetime.now(UTC)
        pa_binding = RoleBinding(
            user_id=admin_seed.user_ids["target"],
            role_id=roles["PA"].id,
            facility_id=None,
            scope_kind="platform",
            starts_at=now,
            ends_at=None,
            granted_by=admin_seed.user_ids["pa"],
        )
        db.session.add(pa_binding)
        db.session.commit()
        pa_binding_id = str(pa_binding.id)

    pa_ok = _post(
        client,
        f"/api/v1/role-bindings/{pa_binding_id}/revoke",
        {"reason": "synthetic pa revoke"},
        expect=200,
    )
    assert pa_ok.json["data"]["ends_at"] is not None

    with admin_seed.app.app_context():
        clinical = db.session.get(RoleBinding, UUID(clinical_id))
        assert clinical is not None and clinical.ends_at is None
        programme = db.session.get(RoleBinding, UUID(programme_id))
        assert programme is not None and programme.ends_at is None
        denied_events = list(
            db.session.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "role.revoke",
                    AuditEvent.outcome == "denied",
                )
            ).all()
        )
        assert any(
            event.event_metadata.get("reason") == "delegation_not_allowed"
            for event in denied_events
        )
