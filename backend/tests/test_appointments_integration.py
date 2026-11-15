"""PostgreSQL appointment transitions, replay, isolation and atomicity regressions."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from test_auth_integration import _csrf, _login
from test_auth_integration import _fresh_rate_limiter as _fresh_rate_limiter
from test_auth_integration import identity as identity
from test_auth_integration import identity_app as identity_app
from test_pregnancy_integration import care_staff as care_staff
from test_pregnancy_integration import owner as owner
from test_pregnancy_integration import pregnancy as pregnancy

from tunza.config import postgres_url
from tunza.extensions import db

pytestmark = pytest.mark.integration


def schedule(p, *, key=None, version=1, client=None, starts_at="2027-06-15T09:00:00+03:00"):
    client = client or p.client
    detail = p.client.get(f"{p.path}?facility_id={p.facility}")
    assert detail.status_code == 200, detail.json
    return client.post(
        f"/api/v1/appointments?facility_id={p.facility}",
        json={
            "episode_facility_assignment_id": detail.json["data"]["assignment_id"],
            "starts_at": starts_at,
        },
        headers={
            "X-CSRF-Token": _csrf(client),
            "Idempotency-Key": key or str(uuid4()),
            **({"If-Match": f'"{version}"'} if version is not None else {}),
        },
    )


def action(p, row, kind, *, body=None, key=None, version=None, client=None):
    client = client or p.client
    return client.post(
        f"/api/v1/appointments/{row['id']}/{kind}?facility_id={p.facility}",
        json=body or {},
        headers={
            "X-CSRF-Token": _csrf(client),
            "Idempotency-Key": key or str(uuid4()),
            "If-Match": f'"{row["version"] if version is None else version}"',
        },
    )


def booking(p):
    response = schedule(p)
    assert response.status_code == 201, response.json
    return response.json["data"]


def test_schedule_confirm_and_staff_recorded_attendance(pregnancy, owner):
    p = pregnancy
    row = booking(p)
    assert row["status"] == "scheduled" and row["confirmed_at"] is None
    assert row["starts_at"] == "2027-06-15T06:00:00Z"
    assert row["episode_version"] == 2 and row["reminder_eligible"] is False
    assert row["reminder_hold_reason"] == "scheduled"
    confirmed = action(p, row, "confirm")
    assert confirmed.status_code == 200, confirmed.json
    row = confirmed.json["data"]
    assert row["version"] == 2 and row["confirmed_by"] and row["confirmed_at"].endswith("Z")
    assert row["reminder_eligible"] is False and row["reminder_hold_reason"] == "D06_unconfigured"
    attendance = action(
        p,
        row,
        "attendance",
        body={
            "status": "attended",
            "effective_at": "2027-06-15T10:00:00+03:00",
            "reason": "Synthetic staff observation",
        },
    )
    assert attendance.status_code == 200, attendance.json
    assert attendance.json["data"]["status"] == "attended"
    assert attendance.json["data"]["attendance_effective_at"] == "2027-06-15T07:00:00Z"
    history = p.client.get(f"/api/v1/appointments/{row['id']}/events?facility_id={p.facility}")
    assert [event["event_type"] for event in history.json["data"]] == [
        "scheduled",
        "confirmed",
        "attended",
    ]
    assert history.json["data"][-1]["data"]["reason"] == "Synthetic staff observation"
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM appointment_events WHERE appointment_id = :id"),
                {"id": row["id"]},
            )
            == 3
        )
        ciphers = (
            connection.execute(
                text("SELECT event_data_cipher FROM appointment_events WHERE appointment_id = :id"),
                {"id": row["id"]},
            )
            .scalars()
            .all()
        )
        assert all(b"Synthetic staff observation" not in value for value in ciphers)


@pytest.mark.parametrize("outcome", ["attended", "missed"])
def test_attendance_is_explicit_staff_action_never_time_inference(pregnancy, outcome):
    p = pregnancy
    row = booking(p)
    invalid = action(
        p, row, "attendance", body={"status": outcome, "effective_at": "2027-06-15T07:00:00Z"}
    )
    assert invalid.status_code == 409
    row = action(p, row, "confirm").json["data"]
    result = action(
        p, row, "attendance", body={"status": outcome, "effective_at": "2027-06-15T07:00:00Z"}
    )
    assert result.status_code == 200 and result.json["data"]["status"] == outcome
    terminal = result.json["data"]
    assert terminal["reminder_eligible"] is False
    assert (
        action(p, terminal, "cancel", body={"reason": "Synthetic cancellation"}).status_code == 409
    )


@pytest.mark.parametrize("confirmed", [True, False])
def test_reschedule_creates_unconfirmed_replacement_preserving_old_history(
    pregnancy, owner, confirmed
):
    p = pregnancy
    original = booking(p)
    if confirmed:
        original = action(p, original, "confirm").json["data"]
    key = str(uuid4())
    payload = {"starts_at": "2027-06-20T11:00:00+03:00", "reason": "Synthetic new booking time"}
    result = action(p, original, "reschedule", key=key, body=payload)
    assert result.status_code == 201, result.json
    replacement = result.json["data"]
    assert replacement["id"] != original["id"] and replacement["supersedes_id"] == original["id"]
    assert replacement["status"] == "scheduled" and replacement["confirmed_at"] is None
    assert replacement["version"] == 1 and replacement["reminder_eligible"] is False
    replay = action(p, original, "reschedule", key=key, body=payload)
    assert replay.status_code == 201 and replay.json == result.json
    old = p.client.get(f"/api/v1/appointments/{original['id']}?facility_id={p.facility}").json[
        "data"
    ]
    assert old["status"] == "rescheduled" and old["starts_at"] == original["starts_at"]
    assert (
        old["superseded_by_id"] == replacement["id"] and old["reminder_hold_reason"] == "superseded"
    )
    assert action(p, old, "confirm").status_code == 409
    assert action(p, old, "reschedule", body=payload).status_code == 409
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM appointments WHERE supersedes_appointment_id = :id"),
                {"id": original["id"]},
            )
            == 1
        )
    original_history = p.client.get(
        f"/api/v1/appointments/{old['id']}/events?facility_id={p.facility}"
    ).json["data"]
    assert original_history[-1]["data"]["related_appointment_id"] == replacement["id"]
    assert original_history[-1]["event_type"] == "rescheduled"


@pytest.mark.parametrize("confirmed", [True, False])
def test_cancel_retains_booking_and_blocks_future_actions(pregnancy, confirmed):
    p = pregnancy
    row = booking(p)
    if confirmed:
        row = action(p, row, "confirm").json["data"]
    cancelled = action(p, row, "cancel", body={"reason": "Synthetic cancellation"})
    assert cancelled.status_code == 200
    row = cancelled.json["data"]
    assert row["status"] == "cancelled" and row["reminder_eligible"] is False
    assert action(p, row, "confirm").status_code == 409
    assert (
        action(p, row, "cancel", body={"reason": "Synthetic second cancellation"}).status_code
        == 409
    )


def test_durable_replay_returns_original_response_after_later_mutations(pregnancy, owner):
    p = pregnancy
    key = str(uuid4())
    first = schedule(p, key=key)
    assert first.status_code == 201, first.json
    row = first.json["data"]
    assert action(p, row, "confirm").status_code == 200
    replay = schedule(p, key=key)
    assert replay.status_code == 201 and replay.json == first.json
    conflict = schedule(p, key=key, starts_at="2027-06-16T09:00:00+03:00")
    assert conflict.status_code == 409
    # Equivalent offset forms canonicalize to the same UTC instant.
    assert schedule(p, key=key, starts_at="2027-06-15T06:00:00Z").json == first.json
    with owner.connect() as connection:
        cipher = connection.scalar(
            text("SELECT response_cipher FROM appointment_idempotency WHERE key = :key"),
            {"key": key},
        )
        assert row["id"].encode() not in cipher
        assert (
            connection.scalar(
                text("SELECT count(*) FROM appointments WHERE episode_id = :id"), {"id": p.episode}
            )
            == 1
        )


@pytest.mark.parametrize(
    "stamp", ["2027-06-15T09:00", "2027-02-29T09:00:00Z", "bad", "2027-06-15T09:00:00+24:00"]
)
def test_timezone_and_calendar_validation(pregnancy, stamp):
    assert schedule(pregnancy, starts_at=stamp).status_code == 422


@pytest.mark.parametrize("status", [[], {}, 1, None])
def test_attendance_rejects_invalid_status_types_without_mutation(pregnancy, status):
    p = pregnancy
    row = action(p, booking(p), "confirm").json["data"]
    response = action(
        p, row, "attendance", body={"status": status, "effective_at": "2027-06-15T07:00:00Z"}
    )
    assert response.status_code == 422
    detail = p.client.get(f"/api/v1/appointments/{row['id']}?facility_id={p.facility}")
    assert detail.json["data"]["status"] == "confirmed" and detail.json["data"]["version"] == 2


def test_parent_versions_business_uniqueness_and_validation(pregnancy):
    p = pregnancy
    assert schedule(p, version=None).status_code == 428
    assert schedule(p, version=0).status_code == 412
    row = booking(p)
    assert schedule(p, version=2).status_code == 409
    assert action(p, row, "confirm", version=0).status_code == 412
    assert action(p, row, "cancel").status_code == 422
    assert (
        action(
            p,
            row,
            "reschedule",
            body={"starts_at": row["starts_at"], "reason": "Synthetic unchanged time"},
        ).status_code
        == 409
    )
    assert action(p, row, "confirm").status_code == 200
    assert action(p, row, "confirm").status_code == 412


@pytest.mark.parametrize(
    "kind",
    ["list", "detail", "history", "schedule", "confirm", "reschedule", "cancel", "attendance"],
)
@pytest.mark.parametrize("login,code", [("stab.other", "TST-A"), ("stab.cross", "TST-B")])
def test_individual_assignment_and_facility_isolation(pregnancy, identity, kind, login, code):
    p = pregnancy
    row = booking(p)
    outsider = identity.app.test_client()
    _login(outsider, login)
    facility = str(identity.facility_ids[code])
    headers = {"X-CSRF-Token": _csrf(outsider), "Idempotency-Key": str(uuid4()), "If-Match": '"1"'}
    if kind == "list":
        result = outsider.get(f"/api/v1/appointments?facility_id={facility}&episode_id={p.episode}")
    elif kind in {"detail", "history"}:
        result = outsider.get(
            f"/api/v1/appointments/{row['id']}{'/events' if kind == 'history' else ''}"
            f"?facility_id={facility}"
        )
    elif kind == "schedule":
        result = outsider.post(
            f"/api/v1/appointments?facility_id={facility}",
            json={
                "episode_facility_assignment_id": row["episode_facility_assignment_id"],
                "starts_at": "2027-06-20T09:00:00Z",
            },
            headers=headers,
        )
    else:
        result = outsider.post(
            f"/api/v1/appointments/{row['id']}/{kind}?facility_id={facility}",
            json={},
            headers=headers,
        )
    assert result.status_code == 404, result.json
    assert "data" not in result.json


def test_permission_denial_and_revoked_assignment_blocks_replay(pregnancy, identity, owner):
    p = pregnancy
    row = booking(p)
    outsider = identity.app.test_client()
    _login(outsider, "tst.pa")
    assert (
        outsider.get(
            f"/api/v1/appointments?facility_id={p.facility}&episode_id={p.episode}"
        ).status_code
        == 403
    )
    key = str(uuid4())
    assert action(p, row, "confirm", key=key).status_code == 200
    with owner.begin() as connection:
        connection.execute(
            text(
                "UPDATE participant_assignments p SET valid_to = now() "
                "FROM episode_facility_assignments a "
                "WHERE a.id = p.episode_facility_assignment_id AND a.episode_id = :id"
            ),
            {"id": p.episode},
        )
    assert action(p, row, "confirm", key=key).status_code == 404


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_schedule_and_confirmation(pregnancy, identity, owner, same_key):
    p = pregnancy
    second = identity.app.test_client()
    _login(second, "stab.hcw")
    assignment = p.client.get(f"{p.path}?facility_id={p.facility}").json["data"]["assignment_id"]
    barrier = Barrier(2)
    key = str(uuid4())

    def submit(client, request_key):
        csrf = _csrf(client)
        barrier.wait(timeout=10)
        return client.post(
            f"/api/v1/appointments?facility_id={p.facility}",
            json={
                "episode_facility_assignment_id": assignment,
                "starts_at": "2027-06-15T06:00:00Z",
            },
            headers={"X-CSRF-Token": csrf, "Idempotency-Key": request_key, "If-Match": '"1"'},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(submit, p.client, key),
            pool.submit(submit, second, key if same_key else str(uuid4())),
        ]
        results = [future.result(timeout=20) for future in futures]
    assert sorted(result.status_code for result in results) == (
        [201, 201] if same_key else [201, 412]
    )
    if same_key:
        assert results[0].json == results[1].json
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM appointments WHERE episode_id = :id"), {"id": p.episode}
            )
            == 1
        )


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_reschedule_has_exactly_one_replacement(pregnancy, identity, owner, same_key):
    p = pregnancy
    row = booking(p)
    second = identity.app.test_client()
    _login(second, "stab.hcw")
    barrier = Barrier(2)
    key = str(uuid4())

    def submit(client, request_key):
        csrf = _csrf(client)
        barrier.wait(timeout=10)
        return client.post(
            f"/api/v1/appointments/{row['id']}/reschedule?facility_id={p.facility}",
            json={
                "starts_at": "2027-06-21T06:00:00Z",
                "reason": "Synthetic concurrent replacement",
            },
            headers={"X-CSRF-Token": csrf, "Idempotency-Key": request_key, "If-Match": '"1"'},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(submit, p.client, key),
            pool.submit(submit, second, key if same_key else str(uuid4())),
        ]
        results = [future.result(timeout=20) for future in futures]
    assert sorted(result.status_code for result in results) == (
        [201, 201] if same_key else [201, 412]
    )
    if same_key:
        assert results[0].json == results[1].json
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM appointments WHERE supersedes_appointment_id = :id"),
                {"id": row["id"]},
            )
            == 1
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM appointment_events WHERE episode_id = :id"),
                {"id": p.episode},
            )
            == 3
        )


def test_concurrent_different_actions_only_one_wins(pregnancy, identity):
    p = pregnancy
    row = booking(p)
    second = identity.app.test_client()
    _login(second, "stab.hcw")
    barrier = Barrier(2)

    def submit(client, kind, body):
        csrf = _csrf(client)
        barrier.wait(timeout=10)
        return client.post(
            f"/api/v1/appointments/{row['id']}/{kind}?facility_id={p.facility}",
            json=body,
            headers={"X-CSRF-Token": csrf, "Idempotency-Key": str(uuid4()), "If-Match": '"1"'},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(submit, p.client, "confirm", {}),
            pool.submit(submit, second, "cancel", {"reason": "Synthetic cancellation"}),
        ]
        results = [future.result(timeout=20) for future in futures]
    assert sorted(result.status_code for result in results) == [200, 412]


def test_command_key_isolation_across_actors_patients_and_operations(pregnancy, identity, owner):
    from test_auth_integration import _assign_pregnancy_staff

    p = pregnancy
    key = str(uuid4())
    first = schedule(p, key=key)
    assert first.status_code == 201
    row = first.json["data"]
    _assign_pregnancy_staff(identity, p.episode, "stab.other")
    second = identity.app.test_client()
    _login(second, "stab.other")
    # The same opaque key is independently scoped to operation and actor.
    assert action(p, row, "confirm", key=key, client=second).status_code == 200
    # A second patient cannot replay a response from the original patient.
    registered = p.client.post(
        "/api/v1/patients",
        json={
            "facility_id": p.facility,
            "local_reference": f"SYN-ADULT-{uuid4().hex}",
            "demographics": {},
            "language": "en",
        },
        headers={"X-CSRF-Token": _csrf(p.client), "Idempotency-Key": str(uuid4())},
    )
    episode_id = registered.json["data"]["pregnancy"]["id"]
    assignment = p.client.get(
        f"/api/v1/pregnancy-episodes/{episode_id}?facility_id={p.facility}"
    ).json["data"]["assignment_id"]
    result = p.client.post(
        f"/api/v1/appointments?facility_id={p.facility}",
        json={"episode_facility_assignment_id": assignment, "starts_at": "2027-06-15T06:00:00Z"},
        headers={"X-CSRF-Token": _csrf(p.client), "Idempotency-Key": key, "If-Match": '"1"'},
    )
    assert result.status_code == 409 and "data" not in result.json
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM appointments WHERE episode_id = :id"), {"id": episode_id}
            )
            == 0
        )


def test_runtime_rejects_direct_unassigned_insert(pregnancy, owner):
    p = pregnancy
    row = booking(p)
    with owner.connect() as connection:
        outsider = connection.scalar(
            text("SELECT id FROM users WHERE login_identifier = 'stab.other'")
        )
    runtime = create_engine(postgres_url(os.environ["TEST_DATABASE_URL"]))
    try:
        with pytest.raises(DBAPIError) as error, runtime.begin() as connection:
            connection.execute(
                text(
                    "SELECT set_config('tunza.actor_id', :actor, true), "
                    "set_config('tunza.facility_id', :facility, true), "
                    "set_config('tunza.patient_id', :patient, true)"
                ),
                {"actor": str(outsider), "facility": p.facility, "patient": p.patient},
            )
            connection.execute(
                text("""
                INSERT INTO appointments (id, episode_id, patient_id, facility_id,
                    episode_facility_assignment_id, starts_at, status, created_by)
                VALUES (:id, :episode, :patient, :facility, :assignment,
                        '2027-06-20T06:00:00Z', 'scheduled', :actor)
            """),
                {
                    "id": uuid4(),
                    "episode": p.episode,
                    "patient": p.patient,
                    "facility": p.facility,
                    "assignment": row["episode_facility_assignment_id"],
                    "actor": outsider,
                },
            )
        assert error.value.orig.sqlstate == "42501"
    finally:
        runtime.dispose()


def test_completed_episode_and_past_time_never_enable_reminders(pregnancy, owner):
    p = pregnancy
    row = schedule(p, starts_at="2020-01-01T06:00:00Z").json["data"]
    result = action(p, row, "confirm")
    assert result.status_code == 200
    assert result.json["data"]["reminder_hold_reason"] == "appointment_not_future"
    with owner.begin() as connection:
        connection.execute(
            text("UPDATE pregnancy_episodes SET lifecycle = 'completed' WHERE id = :id"),
            {"id": p.episode},
        )
    detail = p.client.get(f"/api/v1/appointments/{row['id']}?facility_id={p.facility}").json["data"]
    assert (
        detail["reminder_eligible"] is False
        and detail["reminder_hold_reason"] == "episode_completed"
    )
    assert schedule(p, version=2, starts_at="2027-06-21T06:00:00Z").status_code == 409
    assert (
        action(
            p, detail, "cancel", body={"reason": "Synthetic completed episode cleanup"}
        ).status_code
        == 200
    )


def test_replacement_conflicting_with_existing_booking_rolls_back_original(pregnancy, owner):
    p = pregnancy
    first = booking(p)
    second = schedule(p, version=2, starts_at="2027-06-20T06:00:00Z")
    assert second.status_code == 201
    key = str(uuid4())
    result = action(
        p,
        first,
        "reschedule",
        key=key,
        body={"starts_at": "2027-06-20T06:00:00Z", "reason": "Synthetic slot conflict"},
    )
    assert result.status_code == 409
    with owner.connect() as connection:
        row = connection.execute(
            text("SELECT status, version FROM appointments WHERE id = :id"), {"id": first["id"]}
        ).one()
        assert row.status == "scheduled" and row.version == 1
        assert (
            connection.scalar(
                text("SELECT count(*) FROM appointment_events WHERE appointment_id = :id"),
                {"id": first["id"]},
            )
            == 1
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM appointment_idempotency WHERE key = :key"), {"key": key}
            )
            == 0
        )


@pytest.mark.parametrize("kind", ["confirm", "reschedule", "cancel", "attendance"])
def test_audit_failure_rolls_back_entire_action_and_key(pregnancy, owner, monkeypatch, kind):
    import tunza.appointments.routes as routes

    p = pregnancy
    row = booking(p)
    if kind == "attendance":
        row = action(p, row, "confirm").json["data"]
    key = str(uuid4())
    body = {
        "confirm": {},
        "cancel": {"reason": "Synthetic audit test"},
        "reschedule": {"starts_at": "2027-06-21T06:00:00Z", "reason": "Synthetic audit test"},
        "attendance": {"status": "attended", "effective_at": "2027-06-15T07:00:00Z"},
    }[kind]
    old_version = row["version"]

    def fail(*args, **kwargs):
        db.session.flush()
        raise SQLAlchemyError("Synthetic audit failure")

    with monkeypatch.context() as patch:
        patch.setattr(routes, "_audit", fail)
        failed = action(p, row, kind, body=body, key=key)
        assert failed.status_code == 503 and "data" not in failed.json
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT version FROM appointments WHERE id = :id"), {"id": row["id"]}
            )
            == old_version
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM appointments WHERE episode_id = :id"), {"id": p.episode}
            )
            == 1
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM appointment_events WHERE appointment_id = :id"),
                {"id": row["id"]},
            )
            == old_version
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM appointment_idempotency WHERE key = :key"), {"key": key}
            )
            == 0
        )
    assert action(p, row, kind, body=body, key=key).status_code in {200, 201}


def test_schedule_audit_rollback_and_read_audit_failure(pregnancy, owner, monkeypatch):
    import tunza.appointments.routes as routes

    p = pregnancy
    key = str(uuid4())

    def fail(*args, **kwargs):
        db.session.flush()
        raise SQLAlchemyError("Synthetic audit failure")

    with monkeypatch.context() as patch:
        patch.setattr(routes, "_audit", fail)
        assert schedule(p, key=key).status_code == 503
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM appointments WHERE episode_id = :id"), {"id": p.episode}
            )
            == 0
        )
        assert (
            connection.scalar(
                text("SELECT version FROM pregnancy_episodes WHERE id = :id"), {"id": p.episode}
            )
            == 1
        )
    row = schedule(p, key=key).json["data"]
    with monkeypatch.context() as patch:
        patch.setattr(routes, "_audit", fail)
        result = p.client.get(f"/api/v1/appointments/{row['id']}?facility_id={p.facility}")
        assert result.status_code == 503 and "data" not in result.json


def test_list_filters_and_cursor_scope(pregnancy):
    p = pregnancy
    first = booking(p)
    second = schedule(p, version=2, starts_at="2027-06-16T09:00:00+03:00").json["data"]
    path = f"/api/v1/appointments?facility_id={p.facility}&episode_id={p.episode}&limit=1"
    page = p.client.get(path)
    assert [row["id"] for row in page.json["data"]] == [first["id"]]
    cursor = page.json["page"]["next_cursor"]
    assert cursor
    next_page = p.client.get(path + f"&cursor={cursor}")
    assert [row["id"] for row in next_page.json["data"]] == [second["id"]]
    assert next_page.json["page"]["next_cursor"] is None
    assert p.client.get(path + f"&status=confirmed&cursor={cursor}").status_code == 422
    assert p.client.get(path + "&status=invalid").status_code == 422
    assert p.client.get(path + "&from=2027-06-16T00:00:00Z").json["data"][0]["id"] == second["id"]


def test_postgres_rls_and_immutable_events(pregnancy, owner):
    p = pregnancy
    row = booking(p)
    with owner.connect() as connection:
        other = connection.scalar(
            text("SELECT id FROM users WHERE login_identifier = 'stab.other'")
        )
    runtime = create_engine(
        postgres_url(os.environ["TEST_DATABASE_URL"]), pool_size=1, max_overflow=0
    )
    try:
        for actor in (None, other):
            with runtime.begin() as connection:
                connection.execute(
                    text(
                        "SELECT set_config('tunza.actor_id', :actor, true), "
                        "set_config('tunza.facility_id', :facility, true), "
                        "set_config('tunza.patient_id', :patient, true)"
                    ),
                    {
                        "actor": str(actor) if actor else "",
                        "facility": p.facility,
                        "patient": p.patient,
                    },
                )
                for table in ("appointments", "appointment_events", "appointment_idempotency"):
                    # This actor may legitimately hold unrelated assignments;
                    # it must never see this patient's protected booking.
                    assert (
                        connection.scalar(
                            text(f"SELECT count(*) FROM {table} WHERE episode_id = :id"),
                            {"id": p.episode},
                        )
                        == 0
                    )
        for table in ("appointment_events", "appointment_idempotency"):
            for command in (f"UPDATE {table} SET key_version = 'changed'", f"DELETE FROM {table}"):
                with pytest.raises(DBAPIError, match="append-only"), owner.begin() as connection:
                    connection.execute(text(command))
        with (
            pytest.raises(DBAPIError, match="Invalid appointment transition"),
            owner.begin() as connection,
        ):
            connection.execute(
                text("UPDATE appointments SET status = 'attended', version = 2 WHERE id = :id"),
                {"id": row["id"]},
            )
        with (
            pytest.raises(DBAPIError, match="matching append-only history"),
            owner.begin() as connection,
        ):
            connection.execute(
                text("UPDATE appointments SET status = 'cancelled', version = 2 WHERE id = :id"),
                {"id": row["id"]},
            )
        with runtime.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM appointments")) == 0
    finally:
        runtime.dispose()
