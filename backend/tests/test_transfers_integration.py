"""A08 fixture acceptance, bounded handover, isolation and transaction safety."""

import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from test_auth_integration import PASSWORD, _csrf, _login
from test_auth_integration import _fresh_rate_limiter as _fresh_rate_limiter
from test_auth_integration import identity as identity
from test_pregnancy_integration import care_staff as care_staff
from test_pregnancy_integration import owner as owner
from test_pregnancy_integration import pregnancy as pregnancy

from tunza import create_app
from tunza.config import postgres_url
from tunza.extensions import db
from tunza.fixture_database import DECIDE, FIXTURE_ROLE, PRIVATE, provision_fixture_role
from tunza.identity.models import FacilityMembership, Role, RoleBinding, User
from tunza.identity.passwords import hash_password

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def identity_app():
    """Fixture transfers authenticate with a separate, non-inheriting DB login.

    All other integration modules keep testing the standard runtime principal.
    Owner credentials are used only for schema/provisioning, never by the app.
    """
    base_value = os.environ.get("TEST_DATABASE_URL")
    if not base_value:
        pytest.skip("Dedicated disposable PostgreSQL test URLs are required.")
    owner_value = os.environ.get("TEST_MIGRATION_DATABASE_URL")
    fixture_value = os.environ.get("TEST_FIXTURE_DATABASE_URL")
    assert owner_value and fixture_value, "Set owner and separate TEST_FIXTURE_DATABASE_URL logins."
    base, owner_url, fixture = (
        postgres_url(value) for value in (base_value, owner_value, fixture_value)
    )
    assert base.host in {"127.0.0.1", "localhost", "::1"}
    assert base.database.startswith("tunza_") and base.database.endswith("_test")
    assert fixture.username == FIXTURE_ROLE
    assert owner_url.username not in {base.username, FIXTURE_ROLE}
    assert (fixture.host, fixture.port, fixture.database) == (base.host, base.port, base.database)
    assert (owner_url.host, owner_url.port, owner_url.database) == (
        base.host,
        base.port,
        base.database,
    )
    settings = {
        "APP_ENV": "testing",
        "SECRET_KEY": secrets.token_urlsafe(48),
        "DATABASE_URL": base_value,
        "MIGRATION_DATABASE_URL": owner_value,
        "SESSION_COOKIE_SECURE": False,
    }
    bootstrap = create_app(settings)
    runner = bootstrap.test_cli_runner()
    with bootstrap.app_context():
        if inspect(db.engine).has_table("alembic_version"):
            result = runner.invoke(args=["db", "downgrade", "base"])
            assert result.exit_code == 0, result.output
        result = runner.invoke(args=["db", "upgrade"])
        assert result.exit_code == 0, result.output
        db.session.remove()
        db.engine.dispose()
    provision_fixture_role(owner_value, environment="testing")
    application = create_app(
        {
            **settings,
            "DATABASE_URL": fixture_value,
            "DATABASE_RUNTIME_ROLE": FIXTURE_ROLE,
            "SYNTHETIC_TRANSFERS_ENABLED": True,
        }
    )
    yield application
    with application.app_context():
        db.session.remove()
        result = application.test_cli_runner().invoke(args=["db", "downgrade", "base"])
        assert result.exit_code == 0, result.output
        db.engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def transfer_staff(identity, care_staff):
    identity.app.config["SYNTHETIC_TRANSFERS_ENABLED"] = True
    with identity.app.app_context():
        role = db.session.scalar(select(Role).where(Role.code == "FA"))
        for name, facility in (("a", "TST-A"), ("b", "TST-B"), ("c", "TST-C")):
            user = User(
                id=uuid4(),
                login_identifier=f"transfer.fa.{name}",
                display_name="Synthetic transfer administrator",
                password_hash=hash_password(PASSWORD),
                status="active",
            )
            db.session.add(user)
            db.session.flush()
            db.session.add(
                FacilityMembership(
                    user_id=user.id,
                    facility_id=identity.facility_ids[facility],
                    status="active",
                    starts_at=datetime.now(UTC),
                )
            )
            db.session.add(
                RoleBinding(
                    user_id=user.id,
                    role_id=role.id,
                    facility_id=identity.facility_ids[facility],
                    scope_kind="facility",
                    starts_at=datetime.now(UTC),
                    granted_by=identity.user_ids["pa"],
                )
            )
        db.session.commit()


def actor(identity, login):
    client = identity.app.test_client()
    _login(client, login)
    return client


def initiate(p, identity, *, client=None, key=None, version=None, **changes):
    client = client or p.client
    detail = p.client.get(f"{p.path}?facility_id={p.facility}")
    assert detail.status_code == 200, detail.json
    return client.post(
        f"/api/v1/transfers?facility_id={p.facility}",
        json={
            "source_assignment_id": detail.json["data"]["assignment_id"],
            "destination_facility_id": str(identity.facility_ids["TST-B"]),
            "handover": {"synthetic": True, "fixture_code": "SYN-HANDOVER-TEST"},
            **changes,
        },
        headers={
            "X-CSRF-Token": _csrf(client),
            "Idempotency-Key": key or str(uuid4()),
            "If-Match": f'"{detail.json["data"]["version"] if version is None else version}"',
        },
    )


def requested(p, identity):
    result = initiate(p, identity)
    assert result.status_code == 201, result.json
    return result.json["data"]


def decision(
    p, identity, owner, row, *, choice="accept", key=None, client=None, version=1, **changes
):
    client = client or actor(identity, "transfer.fa.b")
    facility = str(identity.facility_ids["TST-B"])
    body = {"decision": choice, "reason": "SYN-DECISION-TEST", **changes}
    if choice == "accept":
        with owner.connect() as connection:
            member = connection.scalar(
                text(
                    "SELECT m.id FROM facility_memberships m JOIN users u ON u.id = m.user_id "
                    "WHERE u.login_identifier = 'stab.cross' AND m.facility_id = :facility"
                ),
                {"facility": facility},
            )
        body = {"receiver_membership_id": str(member), "synthetic_language": "lg", **body}
    return client.post(
        f"/api/v1/transfers/{row['id']}/decision?facility_id={facility}",
        json=body,
        headers={
            "X-CSRF-Token": _csrf(client),
            "Idempotency-Key": key or str(uuid4()),
            "If-Match": f'"{version}"',
        },
    )


def test_acceptance_atomically_moves_responsibility_without_copying_source_history(
    pregnancy, identity, owner
):
    from test_appointments_integration import booking
    from test_pregnancy_integration import dating

    p = pregnancy
    old_booking = booking(p)
    assert dating(p, version=2).status_code == 201
    from test_auth_integration import _create_consent_fixtures

    source_actor = p.client.get("/api/v1/auth/me").json["data"]["user_id"]
    notice, evidence = _create_consent_fixtures(
        identity.app, p.facility, source_actor, p.patient, "TRANSFER-" + uuid4().hex[:8]
    )
    consent = p.client.post(
        f"/api/v1/patients/{p.patient}/consent-events?facility_id={p.facility}",
        json={
            "purpose": "messaging",
            "decision": "grant",
            "notice_version_id": str(notice),
            "evidence_id": str(evidence),
            "method": "documentary",
            "language": "en",
        },
        headers={"X-CSRF-Token": _csrf(p.client), "Idempotency-Key": str(uuid4())},
    )
    assert consent.status_code == 201
    with owner.begin() as connection:
        connection.execute(
            text("UPDATE pregnancy_episodes SET communication_state = 'active' WHERE id = :id"),
            {"id": p.episode},
        )
    row = requested(p, identity)
    destination_admin = actor(identity, "transfer.fa.b")
    destination_care = actor(identity, "stab.cross")
    facility = str(identity.facility_ids["TST-B"])
    assert destination_care.get(f"{p.path}?facility_id={facility}").status_code == 404
    fixture = destination_admin.get(
        f"/api/v1/transfers/{row['id']}/handover?facility_id={facility}"
    )
    assert fixture.json["data"] == {"synthetic": True, "fixture_code": "SYN-HANDOVER-TEST"}
    result = decision(p, identity, owner, row, client=destination_admin)
    assert result.status_code == 200, result.json
    assert result.json["data"]["state"] == "accepted"
    receipt = result.json["data"]["decision"]
    assert receipt["destination_reference"].startswith("SYN-ADULT-TRANSFER-")
    with owner.connect() as connection:
        assignments = connection.execute(
            text(
                "SELECT id, facility_id, valid_from, valid_to "
                "FROM episode_facility_assignments WHERE episode_id = :id ORDER BY valid_from"
            ),
            {"id": p.episode},
        ).all()
        assert len(assignments) == 2
        assert assignments[0].valid_to == assignments[1].valid_from
        assert assignments[1].valid_to is None and str(assignments[1].facility_id) == facility
        assert (
            connection.scalar(
                text("SELECT count(*) FROM appointments WHERE id = :id"), {"id": old_booking["id"]}
            )
            == 1
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM pregnancy_dating_revisions WHERE episode_id = :id"),
                {"id": p.episode},
            )
            == 1
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM consent_events WHERE patient_id = :id "
                    "AND facility_id = :facility"
                ),
                {"id": p.patient, "facility": facility},
            )
            == 0
        )
    assert p.client.get(f"{p.path}?facility_id={p.facility}").status_code == 404
    assert (
        p.client.get(
            f"/api/v1/appointments/{old_booking['id']}?facility_id={p.facility}"
        ).status_code
        == 404
    )
    assert (
        destination_care.get(
            f"/api/v1/appointments/{old_booking['id']}?facility_id={facility}"
        ).status_code
        == 404
    )
    detail = destination_care.get(f"{p.path}?facility_id={facility}")
    assert detail.status_code == 200 and detail.json["data"]["communication_state"] == "paused"
    assert detail.json["data"]["dating_summary"] is None
    profile = destination_care.get(f"/api/v1/patients/{p.patient}?facility_id={facility}")
    assert profile.status_code == 200 and profile.json["data"]["demographics"] == {}
    assert profile.json["data"]["language"] == "lg"
    assert profile.json["data"]["local_reference"] == receipt["destination_reference"]
    from types import SimpleNamespace

    from test_appointments_integration import schedule

    destination_context = SimpleNamespace(client=destination_care, facility=facility, path=p.path)
    future = schedule(destination_context, version=detail.json["data"]["version"])
    assert future.status_code == 201, future.json
    assert (
        p.client.get(
            f"/api/v1/appointments/{future.json['data']['id']}?facility_id={p.facility}"
        ).status_code
        == 404
    )
    assert (
        p.client.get(
            f"/api/v1/appointments/{future.json['data']['id']}?facility_id={facility}"
        ).status_code
        == 404
    )
    source_receipt = p.client.get(f"/api/v1/transfers/{row['id']}?facility_id={p.facility}")
    assert source_receipt.status_code == 200
    assert "decision" not in source_receipt.json["data"]
    assert receipt["destination_reference"] not in source_receipt.get_data(as_text=True)


@pytest.mark.parametrize("outcome", ["reject", "cancel"])
def test_rejection_and_cancellation_preserve_source_responsibility(
    pregnancy, identity, owner, outcome
):
    p = pregnancy
    row = requested(p, identity)
    if outcome == "reject":
        result = decision(p, identity, owner, row, choice="reject")
    else:
        result = p.client.post(
            f"/api/v1/transfers/{row['id']}/cancel?facility_id={p.facility}",
            json={"reason": "SYN-CANCEL-TEST"},
            headers={
                "X-CSRF-Token": _csrf(p.client),
                "Idempotency-Key": str(uuid4()),
                "If-Match": '"1"',
            },
        )
    assert result.status_code == 200, result.json
    assert result.json["data"]["state"] == ("rejected" if outcome == "reject" else "cancelled")
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM episode_facility_assignments "
                    "WHERE episode_id = :id AND valid_to IS NULL"
                ),
                {"id": p.episode},
            )
            == 1
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM facility_patient_profiles WHERE patient_id = :id"),
                {"id": p.patient},
            )
            == 1
        )
    assert p.client.get(f"{p.path}?facility_id={p.facility}").status_code == 200


def test_default_and_production_policy_gates(pregnancy, identity, monkeypatch):
    p = pregnancy
    with monkeypatch.context() as patch:
        patch.setitem(identity.app.config, "SYNTHETIC_TRANSFERS_ENABLED", False)
        capabilities = p.client.get("/api/v1/transfers/capabilities")
        assert capabilities.json["data"] == {
            "synthetic_enabled": False,
            "live_enabled": False,
            "unresolved_decisions": ["D04", "D09"],
        }
        assert initiate(p, identity).status_code == 403
    with monkeypatch.context() as patch:
        patch.setitem(identity.app.config, "APP_ENV", "production")
        assert initiate(p, identity).status_code == 403


@pytest.mark.parametrize(
    "login,facility_code",
    [("stab.other", "TST-A"), ("stab.cross", "TST-B"), ("transfer.fa.c", "TST-C")],
)
def test_scoped_request_handover_and_destination_records(
    pregnancy, identity, owner, login, facility_code
):
    p = pregnancy
    row = requested(p, identity)
    outsider = actor(identity, login)
    facility = str(identity.facility_ids[facility_code])
    for suffix in ("", "/handover", "/receivers"):
        result = outsider.get(f"/api/v1/transfers/{row['id']}{suffix}?facility_id={facility}")
        assert result.status_code in {403, 404}, result.json
        assert "data" not in result.json


def test_replay_conflicts_and_revocation_safe_receipts(pregnancy, identity, owner):
    p = pregnancy
    key = str(uuid4())
    first = initiate(p, identity, key=key, version=1)
    assert first.status_code == 201, first.json
    assert initiate(p, identity, key=key, version=1).json == first.json
    conflict = initiate(
        p, identity, key=key, version=1, handover={"synthetic": True, "fixture_code": "SYN-OTHER"}
    )
    assert conflict.status_code == 409
    row = first.json["data"]
    decision_key = str(uuid4())
    receiver = actor(identity, "transfer.fa.b")
    accepted = decision(p, identity, owner, row, key=decision_key, client=receiver)
    assert accepted.status_code == 200, accepted.json
    replay = decision(p, identity, owner, row, key=decision_key, client=receiver)
    assert replay.json == accepted.json
    assert (
        decision(
            p, identity, owner, row, key=decision_key, client=receiver, choice="reject"
        ).status_code
        == 409
    )
    # Only the original initiator can replay its old bounded source receipt.
    replay_source = p.client.post(
        f"/api/v1/transfers?facility_id={p.facility}",
        json={
            "source_assignment_id": row["source_assignment_id"],
            "destination_facility_id": row["destination_facility_id"],
            "handover": {"synthetic": True, "fixture_code": "SYN-HANDOVER-TEST"},
        },
        headers={"X-CSRF-Token": _csrf(p.client), "Idempotency-Key": key, "If-Match": '"1"'},
    )
    assert replay_source.status_code == 201 and replay_source.json == first.json


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_acceptance_has_one_destination_assignment(pregnancy, identity, owner, same_key):
    p = pregnancy
    row = requested(p, identity)
    clients = [actor(identity, "transfer.fa.b"), actor(identity, "transfer.fa.b")]
    barrier = Barrier(2)
    key = str(uuid4())

    def submit(client, request_key):
        barrier.wait(timeout=10)
        return decision(p, identity, owner, row, key=request_key, client=client)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(submit, clients[0], key),
            pool.submit(submit, clients[1], key if same_key else str(uuid4())),
        ]
        results = [future.result(timeout=30) for future in futures]
    assert sorted(result.status_code for result in results) == (
        [200, 200] if same_key else [200, 412]
    ), [r.json for r in results]
    if same_key:
        assert results[0].json == results[1].json
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM transfer_decisions WHERE transfer_id = :id"),
                {"id": row["id"]},
            )
            == 1
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM episode_facility_assignments "
                    "WHERE episode_id = :id AND valid_to IS NULL"
                ),
                {"id": p.episode},
            )
            == 1
        )


def test_acceptance_audit_failure_rolls_back_every_cross_facility_write(
    pregnancy, identity, owner, monkeypatch
):
    import tunza.transfers.routes as routes

    p = pregnancy
    row = requested(p, identity)
    key = str(uuid4())
    receiver = actor(identity, "transfer.fa.b")

    def fail(*args, **kwargs):
        db.session.flush()
        raise SQLAlchemyError("Synthetic audit failure")

    with monkeypatch.context() as patch:
        patch.setattr(routes, "_audit", fail)
        result = decision(p, identity, owner, row, key=key, client=receiver)
        assert result.status_code == 503, result.json
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT state FROM facility_transfers WHERE id = :id"), {"id": row["id"]}
            )
            == "pending"
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM transfer_decisions WHERE transfer_id = :id"),
                {"id": row["id"]},
            )
            == 0
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM facility_patient_profiles WHERE patient_id = :id"),
                {"id": p.patient},
            )
            == 1
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM episode_facility_assignments WHERE episode_id = :id"),
                {"id": p.episode},
            )
            == 1
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM transfer_idempotency WHERE key = :key"), {"key": key}
            )
            == 0
        )
    assert decision(p, identity, owner, row, key=key, client=receiver).status_code == 200


@pytest.mark.parametrize(
    "handover",
    [
        {"synthetic": True, "fixture_code": "SYN-TEST", "clinical_notes": "not-allowed"},
        {"synthetic": False, "fixture_code": "SYN-TEST"},
        {"synthetic": 1, "fixture_code": "SYN-TEST"},
        {"synthetic": True, "fixture_code": "unapproved narrative"},
        [],
        None,
    ],
)
def test_unapproved_handover_fields_and_agreement_claims_are_rejected(
    pregnancy, identity, handover
):
    assert initiate(pregnancy, identity, handover=handover).status_code == 422
    assert initiate(pregnancy, identity, agreement_evidence_id=str(uuid4())).status_code == 422


def test_parent_versions_pending_uniqueness_and_invalid_receiver(pregnancy, identity, owner):
    p = pregnancy
    assert initiate(p, identity, version=0).status_code == 412
    assert initiate(p, identity, destination_facility_id=p.facility).status_code == 422
    row = requested(p, identity)
    assert initiate(p, identity).status_code == 409
    assert decision(p, identity, owner, row, version=0).status_code == 412
    assert decision(p, identity, owner, row, receiver_membership_id=str(uuid4())).status_code == 422
    source_admin = actor(identity, "transfer.fa.a")
    result = source_admin.post(
        f"/api/v1/transfers/{row['id']}/decision?facility_id={p.facility}",
        json={"decision": "reject", "reason": "SYN-WRONG-SIDE"},
        headers={
            "X-CSRF-Token": _csrf(source_admin),
            "Idempotency-Key": str(uuid4()),
            "If-Match": '"1"',
        },
    )
    assert result.status_code == 404
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM transfer_decisions WHERE transfer_id = :id"),
                {"id": row["id"]},
            )
            == 0
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM facility_transfers WHERE source_assignment_id = :id "
                    "AND state = 'pending'"
                ),
                {"id": row["source_assignment_id"]},
            )
            == 1
        )


def test_withdrawn_communication_is_never_reactivated(pregnancy, identity, owner):
    p = pregnancy
    with owner.begin() as connection:
        connection.execute(
            text("UPDATE pregnancy_episodes SET communication_state = 'withdrawn' WHERE id = :id"),
            {"id": p.episode},
        )
    row = requested(p, identity)
    assert decision(p, identity, owner, row).status_code == 200
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT communication_state FROM pregnancy_episodes WHERE id = :id"),
                {"id": p.episode},
            )
            == "withdrawn"
        )


def test_receiver_must_retain_actual_care_permissions(pregnancy, identity, owner):
    p = pregnancy
    row = requested(p, identity)
    with owner.begin() as connection:
        grant = connection.execute(
            text(
                "SELECT rp.role_id, rp.permission_id FROM role_permissions rp "
                "JOIN roles r ON r.id = rp.role_id JOIN permissions p ON p.id = rp.permission_id "
                "WHERE r.code = 'HCW' AND p.code = 'patient.read'"
            )
        ).one()
        connection.execute(
            text(
                "DELETE FROM role_permissions WHERE role_id = :role AND permission_id = :permission"
            ),
            {"role": grant.role_id, "permission": grant.permission_id},
        )
    try:
        assert decision(p, identity, owner, row).status_code == 422
    finally:
        with owner.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO role_permissions (role_id,permission_id) "
                    "VALUES (:role,:permission)"
                ),
                {"role": grant.role_id, "permission": grant.permission_id},
            )


def test_revoked_destination_membership_cannot_replay_acceptance(pregnancy, identity, owner):
    p = pregnancy
    row = requested(p, identity)
    receiver = actor(identity, "transfer.fa.b")
    key = str(uuid4())
    assert decision(p, identity, owner, row, client=receiver, key=key).status_code == 200
    with owner.begin() as connection:
        member = connection.scalar(
            text(
                "SELECT m.id FROM facility_memberships m JOIN users u ON u.id = m.user_id "
                "WHERE u.login_identifier = 'transfer.fa.b'"
            )
        )
        connection.execute(
            text("UPDATE facility_memberships SET status = 'ended' WHERE id = :id"), {"id": member}
        )
    try:
        assert decision(p, identity, owner, row, client=receiver, key=key).status_code == 404
    finally:
        with owner.begin() as connection:
            connection.execute(
                text("UPDATE facility_memberships SET status = 'active' WHERE id = :id"),
                {"id": member},
            )


def test_missing_mutation_versions_fail_without_changing_transfer(pregnancy, identity, owner):
    p = pregnancy
    detail = p.client.get(f"{p.path}?facility_id={p.facility}").json["data"]
    result = p.client.post(
        f"/api/v1/transfers?facility_id={p.facility}",
        json={
            "source_assignment_id": detail["assignment_id"],
            "destination_facility_id": str(identity.facility_ids["TST-B"]),
            "handover": {"synthetic": True, "fixture_code": "SYN-TEST"},
        },
        headers={"X-CSRF-Token": _csrf(p.client), "Idempotency-Key": str(uuid4())},
    )
    assert result.status_code == 428
    row = requested(p, identity)
    receiver = actor(identity, "transfer.fa.b")
    result = receiver.post(
        f"/api/v1/transfers/{row['id']}/decision?facility_id={identity.facility_ids['TST-B']}",
        json={"decision": "reject", "reason": "SYN-TEST"},
        headers={"X-CSRF-Token": _csrf(receiver), "Idempotency-Key": str(uuid4())},
    )
    assert result.status_code == 428


def test_source_administrator_can_cancel_only_its_own_facility_request(pregnancy, identity):
    p = pregnancy
    row = requested(p, identity)
    administrator = actor(identity, "transfer.fa.a")
    key = str(uuid4())
    path = f"/api/v1/transfers/{row['id']}/cancel?facility_id={p.facility}"
    headers = {"X-CSRF-Token": _csrf(administrator), "Idempotency-Key": key, "If-Match": '"1"'}
    first = administrator.post(path, json={"reason": "SYN-CANCEL-FIXTURE"}, headers=headers)
    assert first.status_code == 200 and first.json["data"]["state"] == "cancelled"
    assert (
        administrator.post(path, json={"reason": "SYN-CANCEL-FIXTURE"}, headers=headers).json
        == first.json
    )
    assert p.client.get(f"{p.path}?facility_id={p.facility}").status_code == 200


def test_destination_receivers_and_pending_list_are_bounded(pregnancy, identity):
    p = pregnancy
    row = requested(p, identity)
    receiver = actor(identity, "transfer.fa.b")
    facility = str(identity.facility_ids["TST-B"])
    listing = receiver.get(f"/api/v1/transfers?facility_id={facility}&direction=incoming")
    assert listing.status_code == 200
    assert any(record["id"] == row["id"] for record in listing.json["data"])
    record = next(record for record in listing.json["data"] if record["id"] == row["id"])
    assert not {
        "patient_id",
        "episode_id",
        "source_assignment_id",
        "handover",
        "demographics",
        "consent",
    }.intersection(record)
    options = receiver.get(f"/api/v1/transfers/{row['id']}/receivers?facility_id={facility}")
    assert options.status_code == 200 and options.json["data"]
    assert all(set(record) == {"id", "display_name"} for record in options.json["data"])
    assert (
        receiver.get(f"/api/v1/transfers?facility_id={facility}&state=invalid").status_code == 422
    )


def test_transfer_cursor_is_bound_to_selected_scope_and_filters(pregnancy, identity):
    p = pregnancy
    first = requested(p, identity)
    cancelled = p.client.post(
        f"/api/v1/transfers/{first['id']}/cancel?facility_id={p.facility}",
        json={"reason": "SYN-CANCEL-PAGINATION"},
        headers={
            "X-CSRF-Token": _csrf(p.client),
            "Idempotency-Key": str(uuid4()),
            "If-Match": '"1"',
        },
    )
    assert cancelled.status_code == 200
    second = requested(p, identity)
    path = (
        f"/api/v1/transfers?facility_id={p.facility}&direction=outgoing&state=all"
        f"&episode_id={p.episode}&limit=1"
    )
    page = p.client.get(path)
    assert page.status_code == 200 and page.json["data"][0]["id"] == first["id"]
    cursor = page.json["page"]["next_cursor"]
    assert cursor
    next_page = p.client.get(path + f"&cursor={cursor}")
    assert next_page.json["data"][0]["id"] == second["id"]
    assert next_page.json["page"]["next_cursor"] is None
    assert (
        p.client.get(path.replace("state=all", "state=pending") + f"&cursor={cursor}").status_code
        == 422
    )


@pytest.mark.parametrize("outcome", ["reject", "cancel", "initiate"])
def test_other_audit_failures_do_not_persist_partial_history(
    pregnancy, identity, owner, monkeypatch, outcome
):
    import tunza.transfers.routes as routes

    p = pregnancy
    row = requested(p, identity) if outcome != "initiate" else None
    receiver = actor(identity, "transfer.fa.b")
    key = str(uuid4())

    def fail(*args, **kwargs):
        db.session.flush()
        raise SQLAlchemyError("Synthetic audit failure")

    with monkeypatch.context() as patch:
        patch.setattr(routes, "_audit", fail)
        if outcome == "initiate":
            result = initiate(p, identity, key=key)
        elif outcome == "reject":
            result = decision(p, identity, owner, row, choice="reject", key=key, client=receiver)
        else:
            result = p.client.post(
                f"/api/v1/transfers/{row['id']}/cancel?facility_id={p.facility}",
                json={"reason": "SYN-CANCEL-TEST"},
                headers={
                    "X-CSRF-Token": _csrf(p.client),
                    "Idempotency-Key": key,
                    "If-Match": '"1"',
                },
            )
        assert result.status_code == 503 and "data" not in result.json
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM transfer_idempotency WHERE key = :key"), {"key": key}
            )
            == 0
        )
        if row:
            assert (
                connection.scalar(
                    text("SELECT state FROM facility_transfers WHERE id = :id"), {"id": row["id"]}
                )
                == "pending"
            )
        else:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM facility_transfers WHERE episode_id = :id"),
                    {"id": p.episode},
                )
                == 0
            )


def test_concurrent_rejection_and_cancellation_are_serialized(pregnancy, identity, owner):
    p = pregnancy
    row = requested(p, identity)
    incoming = actor(identity, "transfer.fa.b")
    barrier = Barrier(2)

    def reject():
        barrier.wait(timeout=10)
        return decision(p, identity, owner, row, choice="reject", client=incoming)

    def cancel():
        csrf = _csrf(p.client)
        barrier.wait(timeout=10)
        return p.client.post(
            f"/api/v1/transfers/{row['id']}/cancel?facility_id={p.facility}",
            json={"reason": "SYN-CANCEL-RACE"},
            headers={"X-CSRF-Token": csrf, "Idempotency-Key": str(uuid4()), "If-Match": '"1"'},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(reject), pool.submit(cancel)]
        results = [future.result(timeout=30) for future in futures]
    assert sorted(result.status_code for result in results) == [200, 412], [r.json for r in results]
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM transfer_decisions WHERE transfer_id = :id"),
                {"id": row["id"]},
            )
            == 1
        )


def test_rls_definer_boundaries_immutable_history_and_no_overlap(pregnancy, identity, owner):
    p = pregnancy
    row = requested(p, identity)
    with owner.connect() as connection:
        source_actor = connection.scalar(
            text("SELECT id FROM users WHERE login_identifier = 'stab.hcw'")
        )
        other_actor = connection.scalar(
            text("SELECT id FROM users WHERE login_identifier = 'stab.other'")
        )
        destination_actor = connection.scalar(
            text("SELECT id FROM users WHERE login_identifier = 'transfer.fa.b'")
        )
        procedure = connection.execute(
            text(
                "SELECT prosecdef, proconfig FROM pg_proc "
                "WHERE proname = 'tunza_synthetic_transfer_decide'"
            )
        ).one()
        assert procedure.prosecdef and "row_security=off" in procedure.proconfig
        assert (
            connection.scalar(
                text("SELECT prosecdef FROM pg_proc WHERE oid = CAST(:function AS regprocedure)"),
                {"function": PRIVATE},
            )
            is False
        )
        assert any(
            "search_path=pg_catalog, public, pg_temp" == setting for setting in procedure.proconfig
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM pg_proc p, LATERAL aclexplode(p.proacl) acl "
                    "WHERE p.proname = 'tunza_synthetic_transfer_decide' AND acl.grantee = 0"
                )
            )
            == 0
        )
    runtime = create_engine(
        postgres_url(os.environ["TEST_FIXTURE_DATABASE_URL"]), pool_size=1, max_overflow=0
    )
    try:
        for user in (None, other_actor):
            with runtime.begin() as connection:
                connection.execute(
                    text(
                        "SELECT set_config('tunza.actor_id', :actor, true), "
                        "set_config('tunza.facility_id', :facility, true), "
                        "set_config('tunza.synthetic_transfers','on',true)"
                    ),
                    {"actor": str(user) if user else "", "facility": p.facility},
                )
                assert (
                    connection.scalar(
                        text("SELECT count(*) FROM facility_transfers WHERE id = :id"),
                        {"id": row["id"]},
                    )
                    == 0
                )
        with pytest.raises(DBAPIError) as rejected, runtime.begin() as connection:
            connection.execute(
                text(
                    "SELECT set_config('tunza.actor_id', :actor, true), "
                    "set_config('tunza.facility_id', :facility, true), "
                    "set_config('tunza.synthetic_transfers','on',true)"
                ),
                {"actor": str(source_actor), "facility": p.facility},
            )
            connection.execute(
                text(
                    "SELECT tunza_synthetic_transfer_decide(:id,1,'rejected','SYN-FORGE',"
                    "NULL,NULL,NULL,NULL)"
                ),
                {"id": row["id"]},
            )
        assert rejected.value.orig.sqlstate == "42501"
        accepted = decision(p, identity, owner, row)
        assert accepted.status_code == 200, accepted.json
        for user, facility, expected in (
            (source_actor, p.facility, 0),
            (destination_actor, str(identity.facility_ids["TST-B"]), 1),
        ):
            with runtime.begin() as connection:
                connection.execute(
                    text(
                        "SELECT set_config('tunza.actor_id', :actor, true), "
                        "set_config('tunza.facility_id', :facility, true), "
                        "set_config('tunza.synthetic_transfers','on',true)"
                    ),
                    {"actor": str(user), "facility": facility},
                )
                assert (
                    connection.scalar(
                        text("SELECT count(*) FROM transfer_decisions WHERE transfer_id = :id"),
                        {"id": row["id"]},
                    )
                    == expected
                )
                if expected:
                    assert (
                        connection.scalar(
                            text(
                                "SELECT count(*) FROM facility_patient_profiles "
                                "WHERE patient_id = :patient AND facility_id = :source"
                            ),
                            {"patient": p.patient, "source": p.facility},
                        )
                        == 0
                    )
        for table in ("transfer_handover_records", "transfer_decisions", "transfer_idempotency"):
            with pytest.raises(DBAPIError, match="append-only"), owner.begin() as connection:
                connection.execute(text(f"DELETE FROM {table}"))
        with pytest.raises(DBAPIError) as overlap, owner.begin() as connection:
            connection.execute(
                text("""
                INSERT INTO episode_facility_assignments
                    (id,episode_id,patient_id,facility_id,valid_from,valid_to)
                SELECT :new,episode_id,patient_id,facility_id,valid_from,
                    now() + interval '1 day' FROM episode_facility_assignments WHERE id = :source
            """),
                {"new": uuid4(), "source": row["source_assignment_id"]},
            )
        assert overlap.value.orig.sqlstate == "23P01"
        with runtime.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM facility_transfers")) == 0
    finally:
        runtime.dispose()


def _valid_sql_decision(p, identity, owner, row, outcome="accepted"):
    login = "stab.hcw" if outcome == "cancelled" else "transfer.fa.b"
    with owner.connect() as connection:
        actor_id = connection.scalar(
            text("SELECT id FROM users WHERE login_identifier = :login"), {"login": login}
        )
        member = connection.scalar(
            text(
                "SELECT m.id FROM facility_memberships m JOIN users u ON u.id = m.user_id "
                "WHERE u.login_identifier = 'stab.cross' AND m.facility_id = :facility"
            ),
            {"facility": row["destination_facility_id"]},
        )
    with identity.app.app_context():
        from tunza.transfers.routes import _crypt

        cipher = _crypt({}, "patient-demographics")
    return {
        "id": row["id"],
        "version": 1,
        "outcome": outcome,
        "reason": "SYN-SECURITY-REVIEW",
        "receiver": member if outcome == "accepted" else None,
        "language": "lg" if outcome == "accepted" else None,
        "cipher": cipher,
        "key_version": identity.app.config["PATIENT_KEY_VERSION"],
        "actor": str(actor_id),
        "facility": p.facility if outcome == "cancelled" else row["destination_facility_id"],
    }


def _spoof_mode(connection, parameters):
    connection.execute(
        text(
            "SELECT set_config('tunza.actor_id', :actor, true), "
            "set_config('tunza.facility_id', :facility, true), "
            "set_config('tunza.synthetic_transfers','on',true), "
            "set_config('tunza.app_env','testing',true), "
            "set_config('tunza.fixture_authorized','true',true)"
        ),
        parameters,
    )


SQL_DECIDE = """SELECT public.tunza_synthetic_transfer_decide(
    :id,:version,:outcome,:reason,:receiver,:language,:cipher,:key_version)"""


@pytest.mark.parametrize("outcome", ["accepted", "rejected", "cancelled"])
def test_standard_runtime_cannot_bypass_production_gate_with_forged_gucs(
    pregnancy, identity, owner, outcome
):
    p = pregnancy
    row = requested(p, identity)
    parameters = _valid_sql_decision(p, identity, owner, row, outcome)
    standard = create_engine(postgres_url(os.environ["TEST_DATABASE_URL"]))
    try:
        with standard.connect() as connection:
            assert connection.scalar(text("SELECT session_user")) == "tunza_runtime"
            assert (
                connection.scalar(
                    text("SELECT has_function_privilege(current_user,:function,'EXECUTE')"),
                    {"function": DECIDE},
                )
                is False
            )
            assert (
                connection.scalar(
                    text("SELECT has_function_privilege(current_user,:function,'EXECUTE')"),
                    {"function": PRIVATE},
                )
                is False
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT has_table_privilege(current_user,"
                        "'public.facility_transfers','INSERT')"
                    )
                )
                is False
            )
        with pytest.raises(DBAPIError) as denied, standard.begin() as connection:
            _spoof_mode(connection, parameters)
            connection.execute(text(SQL_DECIDE), parameters)
        assert denied.value.orig.sqlstate == "42501"
        with owner.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT state FROM facility_transfers WHERE id = :id"), parameters
                )
                == "pending"
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM transfer_decisions WHERE transfer_id = :id"),
                    parameters,
                )
                == 0
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM facility_patient_profiles WHERE patient_id = :id"),
                    {"id": p.patient},
                )
                == 1
            )
    finally:
        standard.dispose()


@pytest.mark.parametrize(
    "statement",
    ["SET ROLE tunza_fixture_runtime", "SET SESSION AUTHORIZATION tunza_fixture_runtime"],
)
def test_standard_runtime_cannot_assume_fixture_login(statement, pregnancy):
    standard = create_engine(postgres_url(os.environ["TEST_DATABASE_URL"]))
    try:
        with pytest.raises(DBAPIError) as denied, standard.begin() as connection:
            connection.execute(text(statement))
        assert denied.value.orig.sqlstate == "42501"
    finally:
        standard.dispose()


@pytest.mark.parametrize("url_variable", ["TEST_DATABASE_URL", "TEST_FIXTURE_DATABASE_URL"])
def test_private_transfer_worker_is_not_executable_by_application_logins(
    pregnancy, identity, owner, url_variable
):
    p = pregnancy
    row = requested(p, identity)
    parameters = _valid_sql_decision(p, identity, owner, row)
    application = create_engine(postgres_url(os.environ[url_variable]))
    try:
        with pytest.raises(DBAPIError) as denied, application.begin() as connection:
            _spoof_mode(connection, parameters)
            connection.execute(
                text(
                    SQL_DECIDE.replace(
                        "tunza_synthetic_transfer_decide", "tunza_synthetic_transfer_apply_private"
                    )
                ),
                parameters,
            )
        assert denied.value.orig.sqlstate == "42501"
    finally:
        application.dispose()


@pytest.mark.parametrize("entry", [DECIDE, PRIVATE])
def test_boundary_survives_accidental_execute_grant(pregnancy, identity, owner, entry):
    p = pregnancy
    row = requested(p, identity)
    parameters = _valid_sql_decision(p, identity, owner, row)
    standard = create_engine(postgres_url(os.environ["TEST_DATABASE_URL"]))
    with owner.begin() as connection:
        connection.execute(text(f"GRANT EXECUTE ON FUNCTION {entry} TO tunza_runtime"))
    try:
        with pytest.raises(DBAPIError) as denied, standard.begin() as connection:
            _spoof_mode(connection, parameters)
            sql = (
                SQL_DECIDE
                if entry == DECIDE
                else SQL_DECIDE.replace(
                    "tunza_synthetic_transfer_decide", "tunza_synthetic_transfer_apply_private"
                )
            )
            connection.execute(text(sql), parameters)
        assert denied.value.orig.sqlstate == "42501"
        if entry == DECIDE:
            assert "Fixture execution principal required" in str(denied.value.orig)
        probe = create_app(
            {
                "APP_ENV": "testing",
                "SECRET_KEY": secrets.token_urlsafe(48),
                "DATABASE_URL": os.environ["TEST_DATABASE_URL"],
                "MIGRATION_DATABASE_URL": os.environ["TEST_MIGRATION_DATABASE_URL"],
            }
        )
        with probe.app_context():
            with pytest.raises(RuntimeError, match="unsafe fixture execution capability"):
                db.engine.connect()
            db.engine.dispose()
    finally:
        with owner.begin() as connection:
            connection.execute(text(f"REVOKE ALL ON FUNCTION {entry} FROM tunza_runtime"))
        standard.dispose()


def test_enabling_flask_flag_does_not_authorize_standard_runtime(pregnancy, identity, owner):
    p = pregnancy
    row = requested(p, identity)
    application = create_app(
        {
            "APP_ENV": "testing",
            "SECRET_KEY": identity.app.config["SECRET_KEY"],
            "DATABASE_URL": os.environ["TEST_DATABASE_URL"],
            "MIGRATION_DATABASE_URL": os.environ["TEST_MIGRATION_DATABASE_URL"],
            "SESSION_COOKIE_SECURE": False,
            "SYNTHETIC_TRANSFERS_ENABLED": True,
        }
    )
    try:
        client = application.test_client()
        _login(client, "transfer.fa.b")
        assert (
            client.get("/api/v1/transfers/capabilities").json["data"]["synthetic_enabled"] is False
        )
        response = decision(p, identity, owner, row, choice="reject", client=client)
        assert (
            response.status_code == 403 and response.json["error"]["code"] == "policy_unconfigured"
        )
    finally:
        with application.app_context():
            db.session.remove()
            db.engine.dispose()


def test_security_upgrade_revokes_legacy_grants_without_altering_transfer_data(
    pregnancy, identity, owner
):
    p = pregnancy
    row = requested(p, identity)
    bootstrap = create_app(
        {
            "APP_ENV": "testing",
            "SECRET_KEY": secrets.token_urlsafe(48),
            "DATABASE_URL": os.environ["TEST_DATABASE_URL"],
            "MIGRATION_DATABASE_URL": os.environ["TEST_MIGRATION_DATABASE_URL"],
        }
    )
    runner = bootstrap.test_cli_runner()
    try:
        down = runner.invoke(args=["db", "downgrade", "0011_controlled_transfers"])
        assert down.exit_code == 0, down.output
        with owner.begin() as connection:
            # Downgrades must not automatically reopen the original vulnerability.
            assert (
                connection.scalar(
                    text("SELECT has_function_privilege('tunza_runtime',:function,'EXECUTE')"),
                    {"function": DECIDE},
                )
                is False
            )
            connection.execute(text(f"GRANT EXECUTE ON FUNCTION {DECIDE} TO tunza_runtime"))
        upgraded = runner.invoke(args=["db", "upgrade"])
        assert upgraded.exit_code == 0, upgraded.output
        with owner.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT has_function_privilege('tunza_runtime',:function,'EXECUTE')"),
                    {"function": DECIDE},
                )
                is False
            )
            assert (
                connection.scalar(
                    text("SELECT has_function_privilege('tunza_runtime',:function,'EXECUTE')"),
                    {"function": PRIVATE},
                )
                is False
            )
            assert (
                connection.scalar(
                    text("SELECT state FROM facility_transfers WHERE id = :id"), {"id": row["id"]}
                )
                == "pending"
            )
        provision_fixture_role(os.environ["TEST_MIGRATION_DATABASE_URL"], environment="testing")
        assert decision(p, identity, owner, row).status_code == 200
    finally:
        with bootstrap.app_context():
            db.session.remove()
            db.engine.dispose()
