"""Synthetic PostgreSQL regressions for assignment, atomicity and dating history."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from test_auth_integration import (
    PASSWORD,
    _csrf,
    _login,
)
from test_auth_integration import (
    _fresh_rate_limiter as _fresh_rate_limiter,
)
from test_auth_integration import (
    identity as identity,
)
from test_auth_integration import (
    identity_app as identity_app,
)

from tunza.config import postgres_url
from tunza.extensions import db
from tunza.identity.models import FacilityMembership, Role, RoleBinding, User
from tunza.identity.passwords import hash_password

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def owner(identity):
    engine = create_engine(postgres_url(os.environ["TEST_MIGRATION_DATABASE_URL"]))
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def care_staff(identity):
    with identity.app.app_context():
        role = db.session.scalar(select(Role).where(Role.code == "HCW"))
        for login, facility in (
            ("stab.hcw", "TST-A"),
            ("stab.other", "TST-A"),
            ("stab.cross", "TST-B"),
        ):
            user = User(
                id=uuid4(),
                login_identifier=login,
                display_name="Synthetic care staff",
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
                    scope_kind="facility",
                    facility_id=identity.facility_ids[facility],
                    starts_at=datetime.now(UTC),
                    granted_by=identity.user_ids["pa"],
                )
            )
        db.session.commit()


@pytest.fixture
def pregnancy(identity, care_staff):
    client = identity.app.test_client()
    _login(client, "stab.hcw")
    facility = str(identity.facility_ids["TST-A"])
    response = client.post(
        "/api/v1/patients",
        json={
            "facility_id": facility,
            "local_reference": f"SYN-ADULT-{uuid4().hex}",
            "demographics": {},
            "language": "en",
        },
        headers={"X-CSRF-Token": _csrf(client), "Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 201, response.json
    data = response.json["data"]
    return SimpleNamespace(
        client=client,
        facility=facility,
        patient=data["patient"]["id"],
        episode=data["pregnancy"]["id"],
        path=f"/api/v1/pregnancy-episodes/{data['pregnancy']['id']}",
    )


def dating(pregnancy, *, client=None, key=None, version=1, **changes):
    client = client or pregnancy.client
    return client.post(
        f"{pregnancy.path}/dating-revisions?facility_id={pregnancy.facility}",
        json={
            "basis": "synthetic-recorded-method",
            "estimated_due_date": "2027-06-15",
            "reason": "Synthetic initial estimate",
            **changes,
        },
        headers={
            "X-CSRF-Token": _csrf(client),
            "Idempotency-Key": key or str(uuid4()),
            **({"If-Match": f'"{version}"'} if version is not None else {}),
        },
    )


def count(owner, table, episode):
    column = "id" if table == "pregnancy_episodes" else "episode_id"
    with owner.connect() as connection:
        return connection.scalar(
            text(f"SELECT count(*) FROM {table} WHERE {column} = :id"), {"id": episode}
        )


def test_registration_initial_episode_retrievable_and_duplicate_blocked(pregnancy, owner):
    p = pregnancy
    response = p.client.get(
        f"/api/v1/pregnancy-episodes?patient_id={p.patient}&facility_id={p.facility}"
    )
    assert response.status_code == 200
    assert [row["id"] for row in response.json["data"]] == [p.episode]
    detail = p.client.get(f"{p.path}?facility_id={p.facility}")
    assert detail.headers["ETag"] == '"1"'
    assert detail.json["data"]["assignment_id"]
    assert detail.json["data"]["communication_state"] == "paused"
    duplicate = p.client.post(
        "/api/v1/pregnancy-episodes",
        json={"patient_id": p.patient, "facility_id": p.facility},
        headers={"X-CSRF-Token": _csrf(p.client), "Idempotency-Key": str(uuid4())},
    )
    assert duplicate.status_code == 409
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM pregnancy_episodes WHERE patient_id = :id"),
                {"id": p.patient},
            )
            == 1
        )


def test_corrections_preserve_history_and_increment_parent(pregnancy, owner):
    p = pregnancy
    first = dating(p)
    assert first.status_code == 201, first.json
    second = dating(p, version=2, estimated_due_date="2027-06-20", reason="Synthetic correction")
    assert second.status_code == 201, second.json
    assert second.headers["ETag"] == '"3"'
    assert second.json["data"]["supersedes_id"] == first.json["data"]["id"]
    history = p.client.get(f"{p.path}/dating-revisions?facility_id={p.facility}")
    assert history.status_code == 200
    assert [row["revision_sequence"] for row in history.json["data"]] == [2, 1]
    assert history.json["data"][1] == first.json["data"]
    detail = p.client.get(f"{p.path}?facility_id={p.facility}")
    assert detail.json["data"]["version"] == 3
    assert detail.json["data"]["dating_summary"]["id"] == second.json["data"]["id"]
    assert count(owner, "pregnancy_dating_revisions", p.episode) == 2


@pytest.mark.parametrize("operation", ["list", "read", "history", "create", "date"])
@pytest.mark.parametrize("login,facility_code", [("stab.other", "TST-A"), ("stab.cross", "TST-B")])
def test_patient_and_facility_assignment_denial(
    pregnancy, identity, operation, login, facility_code
):
    p = pregnancy
    outsider = identity.app.test_client()
    _login(outsider, login)
    facility = str(identity.facility_ids[facility_code])
    if operation == "list":
        result = outsider.get(
            f"/api/v1/pregnancy-episodes?patient_id={p.patient}&facility_id={facility}"
        )
    elif operation in {"read", "history"}:
        suffix = "/dating-revisions" if operation == "history" else ""
        result = outsider.get(f"{p.path}{suffix}?facility_id={facility}")
    elif operation == "create":
        result = outsider.post(
            "/api/v1/pregnancy-episodes",
            json={"patient_id": p.patient, "facility_id": facility},
            headers={"X-CSRF-Token": _csrf(outsider), "Idempotency-Key": str(uuid4())},
        )
    else:
        result = outsider.post(
            f"{p.path}/dating-revisions?facility_id={facility}",
            json={},
            headers={"X-CSRF-Token": _csrf(outsider), "Idempotency-Key": str(uuid4())},
        )
    assert result.status_code == 404, result.json
    assert "data" not in result.json


@pytest.mark.parametrize("version,status", [(None, 428), (0, 412)])
def test_dating_requires_current_parent_version(pregnancy, version, status):
    assert dating(pregnancy, version=version).status_code == status


@pytest.mark.parametrize(
    "edd", ["2027-02-29", "2027-06-15T00:00:00+00:00", "20270615", "", "invalid"]
)
def test_date_only_contract_rejects_invalid_dates(pregnancy, edd):
    assert dating(pregnancy, estimated_due_date=edd).status_code == 422


def test_dating_replay_conflict_and_cross_patient_key_isolation(pregnancy, owner, identity):
    p = pregnancy
    key = str(uuid4())
    first = dating(p, key=key)
    assert first.status_code == 201, first.json
    # Completed replay is checked before stale If-Match, after authorization.
    replay = dating(p, key=key, version=1)
    assert replay.status_code == 201 and replay.json == first.json
    assert dating(p, key=key, reason="Different synthetic request").status_code == 409
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT version FROM pregnancy_episodes WHERE id = :id"), {"id": p.episode}
            )
            == 2
        )
    # Another authorized patient cannot return the first patient's evidence.
    other = p.client.post(
        "/api/v1/patients",
        json={
            "facility_id": p.facility,
            "local_reference": f"SYN-ADULT-{uuid4().hex}",
            "demographics": {},
            "language": "en",
        },
        headers={"X-CSRF-Token": _csrf(p.client), "Idempotency-Key": str(uuid4())},
    )
    other_id = other.json["data"]["pregnancy"]["id"]
    other_p = SimpleNamespace(
        client=p.client, facility=p.facility, path=f"/api/v1/pregnancy-episodes/{other_id}"
    )
    assert dating(other_p, key=key).status_code == 409
    assert count(owner, "pregnancy_dating_revisions", other_id) == 0


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_dating_serializes_replay_and_stale_writes(pregnancy, identity, owner, same_key):
    p = pregnancy
    second = identity.app.test_client()
    _login(second, "stab.hcw")
    barrier = Barrier(2)
    key = str(uuid4())

    def submit(client, request_key):
        csrf = _csrf(client)
        barrier.wait(timeout=10)
        return client.post(
            f"{p.path}/dating-revisions?facility_id={p.facility}",
            json={
                "basis": "synthetic",
                "estimated_due_date": "2027-07-01",
                "reason": "Synthetic concurrent test",
            },
            headers={"X-CSRF-Token": csrf, "Idempotency-Key": request_key, "If-Match": '"1"'},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(submit, p.client, key),
            pool.submit(submit, second, key if same_key else str(uuid4())),
        ]
        results = [future.result(timeout=20) for future in futures]
    assert sorted(row.status_code for row in results) == ([201, 201] if same_key else [201, 412])
    if same_key:
        assert results[0].json == results[1].json
    assert count(owner, "pregnancy_dating_revisions", p.episode) == 1


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_subsequent_episode_creation(pregnancy, identity, owner, same_key):
    p = pregnancy
    with owner.begin() as connection:
        connection.execute(
            text("UPDATE pregnancy_episodes SET lifecycle = 'completed' WHERE id = :id"),
            {"id": p.episode},
        )
    second = identity.app.test_client()
    _login(second, "stab.hcw")
    barrier = Barrier(2)
    key = str(uuid4())

    def submit(client, request_key):
        csrf = _csrf(client)
        barrier.wait(timeout=10)
        return client.post(
            "/api/v1/pregnancy-episodes",
            json={"patient_id": p.patient, "facility_id": p.facility},
            headers={"X-CSRF-Token": csrf, "Idempotency-Key": request_key},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(submit, p.client, key),
            pool.submit(submit, second, key if same_key else str(uuid4())),
        ]
        results = [future.result(timeout=20) for future in futures]
    assert sorted(row.status_code for row in results) == ([201, 201] if same_key else [201, 409])
    if same_key:
        assert results[0].json == results[1].json
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM pregnancy_episodes WHERE patient_id = :id "
                    "AND lifecycle = 'ongoing'"
                ),
                {"id": p.patient},
            )
            == 1
        )


def test_audit_failure_rolls_back_revision_version_and_key(pregnancy, owner, monkeypatch):
    import tunza.pregnancy.routes as routes

    p = pregnancy
    key = str(uuid4())

    def fail(**kwargs):
        db.session.flush()  # prove rollback after writes reached PostgreSQL
        raise SQLAlchemyError("Synthetic audit failure")

    with monkeypatch.context() as patch:
        patch.setattr(routes, "_record_pregnancy_audit", fail)
        result = dating(p, key=key)
        assert result.status_code == 503 and "data" not in result.json
    assert count(owner, "pregnancy_dating_revisions", p.episode) == 0
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT version FROM pregnancy_episodes WHERE id = :id"), {"id": p.episode}
            )
            == 1
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM audit_events WHERE resource_id = :id "
                    "AND action = 'pregnancy.date'"
                ),
                {"id": p.episode},
            )
            == 0
        )
    assert dating(p, key=key).status_code == 201


def test_episode_creation_audit_rollback_and_read_fail_closed(pregnancy, owner, monkeypatch):
    import tunza.pregnancy.routes as routes

    p = pregnancy
    with owner.begin() as connection:
        connection.execute(
            text("UPDATE pregnancy_episodes SET lifecycle = 'completed' WHERE id = :id"),
            {"id": p.episode},
        )
    key = str(uuid4())
    headers = {"X-CSRF-Token": _csrf(p.client), "Idempotency-Key": key}
    payload = {"patient_id": p.patient, "facility_id": p.facility}

    def fail(**kwargs):
        db.session.flush()
        raise SQLAlchemyError("Synthetic audit failure")

    with monkeypatch.context() as patch:
        patch.setattr(routes, "_record_pregnancy_audit", fail)
        assert (
            p.client.post("/api/v1/pregnancy-episodes", json=payload, headers=headers).status_code
            == 503
        )
        read = p.client.get(f"{p.path}?facility_id={p.facility}")
        assert read.status_code == 503 and "data" not in read.json
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM pregnancy_episodes WHERE patient_id = :id"),
                {"id": p.patient},
            )
            == 1
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM participant_assignments p "
                    "JOIN episode_facility_assignments a "
                    "ON a.id = p.episode_facility_assignment_id "
                    "WHERE a.patient_id = :id"
                ),
                {"id": p.patient},
            )
            == 1
        )
    result = p.client.post("/api/v1/pregnancy-episodes", json=payload, headers=headers)
    assert result.status_code == 201, result.json


def test_assignment_to_one_episode_does_not_authorize_another(pregnancy, owner, identity):
    p = pregnancy
    with owner.begin() as connection:
        connection.execute(
            text("UPDATE pregnancy_episodes SET lifecycle = 'completed' WHERE id = :id"),
            {"id": p.episode},
        )
    response = p.client.post(
        "/api/v1/pregnancy-episodes",
        json={"patient_id": p.patient, "facility_id": p.facility},
        headers={"X-CSRF-Token": _csrf(p.client), "Idempotency-Key": str(uuid4())},
    )
    new_id = response.json["data"]["id"]
    with owner.begin() as connection:
        connection.execute(
            text(
                "UPDATE participant_assignments p SET valid_to = now() "
                "FROM episode_facility_assignments a WHERE a.id = p.episode_facility_assignment_id "
                "AND a.episode_id = :id"
            ),
            {"id": new_id},
        )
    assert p.client.get(f"{p.path}?facility_id={p.facility}").status_code == 200
    assert (
        p.client.get(f"/api/v1/pregnancy-episodes/{new_id}?facility_id={p.facility}").status_code
        == 404
    )
    assert (
        p.client.get(
            f"/api/v1/pregnancy-episodes/{new_id}/dating-revisions?facility_id={p.facility}"
        ).status_code
        == 404
    )


def test_database_enforces_history_immutability_and_uniqueness(pregnancy, owner):
    p = pregnancy
    assert dating(p).status_code == 201
    for statement in (
        "UPDATE pregnancy_dating_revisions SET reason = 'synthetic change' WHERE episode_id = :id",
        "DELETE FROM pregnancy_dating_revisions WHERE episode_id = :id",
    ):
        with pytest.raises(DBAPIError, match="append-only"), owner.begin() as connection:
            connection.execute(text(statement), {"id": p.episode})
    with pytest.raises(IntegrityError), owner.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO pregnancy_episodes (id, patient_id, facility_id, lifecycle,
                communication_state, request_hash, version)
            SELECT :new, patient_id, facility_id, 'ongoing', 'paused', request_hash, 1
            FROM pregnancy_episodes WHERE id = :id
        """),
            {"new": uuid4(), "id": p.episode},
        )
    with pytest.raises(IntegrityError), owner.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO pregnancy_dating_revisions
                (id, episode_id, patient_id, facility_id, basis, estimated_due_date, reason,
                 recorded_by, idempotency_key, request_hash, parent_version,
                 revision_sequence, version)
            SELECT :new, episode_id, patient_id, facility_id, basis, estimated_due_date, reason,
                recorded_by, idempotency_key, request_hash, parent_version,
                revision_sequence, version
            FROM pregnancy_dating_revisions WHERE episode_id = :id
        """),
            {"new": uuid4(), "id": p.episode},
        )


def test_runtime_rls_missing_context_cross_patient_and_expired_assignment(pregnancy, owner):
    p = pregnancy
    assert dating(p).status_code == 201
    runtime = create_engine(
        postgres_url(os.environ["TEST_DATABASE_URL"]), pool_size=1, max_overflow=0
    )
    try:
        with owner.connect() as connection:
            actor = connection.scalar(
                text("SELECT id FROM users WHERE login_identifier = 'stab.hcw'")
            )
            other = connection.scalar(
                text("SELECT id FROM users WHERE login_identifier = 'stab.other'")
            )
        for user, patient in ((None, None), (other, p.patient), (actor, str(uuid4()))):
            with runtime.begin() as connection:
                connection.execute(
                    text(
                        "SELECT set_config('tunza.actor_id', :actor, true), "
                        "set_config('tunza.facility_id', :facility, true), "
                        "set_config('tunza.patient_id', :patient, true)"
                    ),
                    {
                        "actor": str(user) if user else "",
                        "facility": p.facility,
                        "patient": patient or "",
                    },
                )
                assert (
                    connection.scalar(
                        text(
                            "SELECT count(*) FROM pregnancy_dating_revisions WHERE episode_id = :id"
                        ),
                        {"id": p.episode},
                    )
                    == 0
                )
        with runtime.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM pregnancy_dating_revisions")) == 0
        with owner.begin() as connection:
            connection.execute(
                text(
                    "UPDATE participant_assignments p SET valid_to = now() "
                    "FROM episode_facility_assignments a "
                    "WHERE a.id = p.episode_facility_assignment_id "
                    "AND a.episode_id = :id"
                ),
                {"id": p.episode},
            )
        assert p.client.get(f"{p.path}?facility_id={p.facility}").status_code == 404
        assert dating(p).status_code == 404
    finally:
        runtime.dispose()


def test_rls_rejects_direct_unassigned_episode_and_dating_inserts(pregnancy, owner):
    p = pregnancy
    assert dating(p).status_code == 201
    with owner.begin() as connection:
        actor = connection.scalar(
            text("SELECT id FROM users WHERE login_identifier = 'stab.other'")
        )
        connection.execute(
            text("UPDATE pregnancy_episodes SET lifecycle = 'completed' WHERE id = :id"),
            {"id": p.episode},
        )
    runtime = create_engine(postgres_url(os.environ["TEST_DATABASE_URL"]))
    try:
        statements = [
            """INSERT INTO pregnancy_episodes
                (id, patient_id, facility_id, created_by, request_hash,
                 lifecycle, communication_state)
                VALUES (:id, :patient, :facility, :actor, :hash, 'ongoing', 'paused')""",
            """INSERT INTO pregnancy_dating_revisions
                (id, episode_id, patient_id, facility_id, recorded_by, basis, estimated_due_date,
                 reason, idempotency_key, request_hash, revision_sequence, parent_version)
                VALUES (:id, :episode, :patient, :facility, :actor, 'synthetic', '2027-06-15',
                        'Synthetic RLS probe', :key, :hash, 2, 3)""",
        ]
        for statement in statements:
            with pytest.raises(DBAPIError) as rejected, runtime.begin() as connection:
                connection.execute(
                    text(
                        "SELECT set_config('tunza.actor_id', :actor, true), "
                        "set_config('tunza.facility_id', :facility, true), "
                        "set_config('tunza.patient_id', :patient, true)"
                    ),
                    {"actor": str(actor), "facility": p.facility, "patient": p.patient},
                )
                connection.execute(
                    text(statement),
                    {
                        "id": uuid4(),
                        "patient": p.patient,
                        "facility": p.facility,
                        "actor": actor,
                        "episode": p.episode,
                        "key": str(uuid4()),
                        "hash": b"synthetic-hash",
                    },
                )
            assert rejected.value.orig.sqlstate == "42501"
    finally:
        runtime.dispose()
