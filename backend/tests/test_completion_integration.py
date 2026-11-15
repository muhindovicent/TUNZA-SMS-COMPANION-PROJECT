"""A09 synthetic completion, append-only corrections and lifecycle atomicity."""

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


def complete(p, *, key=None, version=1, client=None, **changes):
    client = client or p.client
    return client.post(
        f"{p.path}/completion?facility_id={p.facility}",
        json={
            "completed_on": "2026-11-15",
            "approved_outcome_fields": {},
            "reason": "Synthetic lifecycle closure",
            **changes,
        },
        headers={
            "X-CSRF-Token": _csrf(client),
            "Idempotency-Key": key or str(uuid4()),
            **({"If-Match": f'"{version}"'} if version is not None else {}),
        },
    )


def test_completion_cancels_future_bookings_preserves_history_and_closes_episode(pregnancy, owner):
    from test_appointments_integration import action, schedule

    p = pregnancy
    scheduled = schedule(p).json["data"]
    confirmed = schedule(p, version=2, starts_at="2027-06-16T06:00:00Z").json["data"]
    confirmed = action(p, confirmed, "confirm").json["data"]
    historical = schedule(p, version=3, starts_at="2020-06-15T06:00:00Z").json["data"]
    attended = schedule(p, version=4, starts_at="2027-06-17T06:00:00Z").json["data"]
    attended = action(p, attended, "confirm").json["data"]
    attended = action(
        p,
        attended,
        "attendance",
        body={"status": "attended", "effective_at": "2027-06-17T06:00:00Z"},
    ).json["data"]
    result = complete(p, version=5)
    assert result.status_code == 201, result.json
    record = result.json["data"]
    assert record["cancelled_appointment_count"] == 2 and result.headers["ETag"] == '"6"'
    assert record["lifecycle"] == "completed" and record["communication_state"] == "completed"
    assert record["messaging_eligible"] is False and record["approved_outcome_fields"] == {}
    detail = p.client.get(f"{p.path}?facility_id={p.facility}")
    assert detail.status_code == 200 and detail.json["data"]["completion_record_id"] == record["id"]
    assert detail.json["data"]["messaging_hold_reason"] == "episode_completed"
    with owner.connect() as connection:
        states = dict(
            connection.execute(
                text("SELECT id::text,status FROM appointments WHERE episode_id = :id"),
                {"id": p.episode},
            ).all()
        )
        assert states[scheduled["id"]] == states[confirmed["id"]] == "cancelled"
        assert states[historical["id"]] == "scheduled" and states[attended["id"]] == "attended"
        assert (
            connection.scalar(
                text("SELECT count(*) FROM appointment_events WHERE completion_record_id = :id"),
                {"id": record["id"]},
            )
            == 2
        )
        encrypted = connection.execute(
            text(
                "SELECT reason_cipher,response_cipher FROM pregnancy_completion_records "
                "WHERE id = :id"
            ),
            {"id": record["id"]},
        ).one()
        assert b"Synthetic lifecycle closure" not in encrypted.reason_cipher
        assert b"Synthetic lifecycle closure" not in encrypted.response_cipher
    events = p.client.get(f"/api/v1/appointments/{confirmed['id']}/events?facility_id={p.facility}")
    assert events.json["data"][-1]["event_type"] == "cancelled"
    assert events.json["data"][-1]["completion_record_id"] == record["id"]
    assert p.client.get(f"{p.path}/completion?facility_id={p.facility}").json["data"] == [record]


def test_withdrawal_and_append_only_corrections_do_not_reopen_or_recancel(pregnancy, owner):
    p = pregnancy
    with owner.begin() as connection:
        connection.execute(
            text("UPDATE pregnancy_episodes SET communication_state = 'withdrawn' WHERE id = :id"),
            {"id": p.episode},
        )
    first = complete(p)
    assert first.status_code == 201, first.json
    initial = first.json["data"]
    second = complete(
        p,
        version=2,
        completed_on="2026-11-14",
        reason="Synthetic date correction",
        supersedes_id=initial["id"],
    )
    assert second.status_code == 201, second.json
    correction = second.json["data"]
    assert correction["closed_at"] == initial["closed_at"]
    assert correction["sequence"] == 2 and correction["cancelled_appointment_count"] == 0
    assert (
        correction["communication_state"] == "withdrawn" and correction["lifecycle"] == "completed"
    )
    history = p.client.get(f"{p.path}/completion?facility_id={p.facility}")
    assert history.json["data"] == [correction, initial]
    assert complete(p, version=3).status_code == 409
    assert complete(p, version=3, supersedes_id=initial["id"]).status_code == 409
    for command in (
        "UPDATE pregnancy_completion_records SET completed_on = '2026-11-01' WHERE id = :id",
        "DELETE FROM pregnancy_completion_records WHERE id = :id",
    ):
        with pytest.raises(DBAPIError, match="append-only"), owner.begin() as connection:
            connection.execute(text(command), {"id": initial["id"]})
    with pytest.raises(DBAPIError, match="cannot be reopened"), owner.begin() as connection:
        connection.execute(
            text("UPDATE pregnancy_episodes SET lifecycle = 'ongoing' WHERE id = :id"),
            {"id": p.episode},
        )


def test_durable_replay_conflict_and_stale_versions(pregnancy):
    p = pregnancy
    key = str(uuid4())
    assert complete(p, version=None).status_code == 428
    assert complete(p, version=0).status_code == 412
    first = complete(p, key=key)
    assert first.status_code == 201, first.json
    assert complete(p, key=key).json == first.json
    assert complete(p, key=key, reason="Different synthetic input").status_code == 409
    corrected = complete(
        p, version=2, supersedes_id=first.json["data"]["id"], reason="Synthetic correction"
    )
    assert corrected.status_code == 201
    replay = complete(p, key=key)
    assert replay.status_code == 201 and replay.json == first.json


@pytest.mark.parametrize(
    "changes",
    [
        {"completed_on": "2026-02-29"},
        {"completed_on": "2026-11-15T00:00:00Z"},
        {"approved_outcome_fields": {"birth_weight": 1}},
        {"approved_outcome_fields": []},
        {"reason": ""},
        {"supersedes_id": "invalid"},
        {"lifecycle": "ongoing"},
    ],
)
def test_unapproved_outcomes_invalid_dates_and_server_fields_rejected(pregnancy, changes):
    assert complete(pregnancy, **changes).status_code == 422


@pytest.mark.parametrize(
    "login,status", [("stab.other", 404), ("stab.cross", 404), ("tst.vht.a", 403), ("tst.pa", 403)]
)
def test_completion_authorization_and_history_isolation(pregnancy, identity, login, status):
    p = pregnancy
    outsider = identity.app.test_client()
    _login(outsider, login)
    assert complete(p, client=outsider).status_code == status
    history = outsider.get(f"{p.path}/completion?facility_id={p.facility}")
    assert history.status_code == status and "data" not in history.json


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_completion_is_single_record(pregnancy, identity, owner, same_key):
    p = pregnancy
    other = identity.app.test_client()
    _login(other, "stab.hcw")
    barrier = Barrier(2)
    key = str(uuid4())

    def submit(client, request_key):
        barrier.wait(timeout=10)
        return complete(p, client=client, key=request_key)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(submit, p.client, key),
            pool.submit(submit, other, key if same_key else str(uuid4())),
        ]
        results = [future.result(timeout=20) for future in futures]
    assert sorted(row.status_code for row in results) == ([201, 201] if same_key else [201, 412]), [
        row.json for row in results
    ]
    if same_key:
        assert results[0].json == results[1].json
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM pregnancy_completion_records WHERE episode_id = :id"),
                {"id": p.episode},
            )
            == 1
        )


def test_audit_failure_rolls_back_record_cancellations_versions_and_key(
    pregnancy, owner, monkeypatch
):
    from test_appointments_integration import schedule

    import tunza.pregnancy.completion as routes

    p = pregnancy
    booking = schedule(p).json["data"]
    key = str(uuid4())

    def fail(**kwargs):
        db.session.flush()
        raise SQLAlchemyError("Synthetic completion audit failure")

    with monkeypatch.context() as patch:
        patch.setattr(routes, "_record_pregnancy_audit", fail)
        response = complete(p, key=key, version=2)
        assert response.status_code == 503 and "data" not in response.json
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM pregnancy_completion_records WHERE episode_id = :id"),
                {"id": p.episode},
            )
            == 0
        )
        state = connection.execute(
            text(
                "SELECT lifecycle,communication_state,version,completion_record_id "
                "FROM pregnancy_episodes WHERE id = :id"
            ),
            {"id": p.episode},
        ).one()
        assert (
            state.lifecycle == "ongoing"
            and state.communication_state == "paused"
            and state.version == 2
            and state.completion_record_id is None
        )
        assert (
            connection.scalar(
                text("SELECT status FROM appointments WHERE id = :id"), {"id": booking["id"]}
            )
            == "scheduled"
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM appointment_events WHERE appointment_id = :id"),
                {"id": booking["id"]},
            )
            == 1
        )
    assert complete(p, key=key, version=2).status_code == 201


def test_completed_episode_rejects_new_bookings_and_dating(pregnancy):
    from test_appointments_integration import schedule
    from test_pregnancy_integration import dating

    p = pregnancy
    assert complete(p).status_code == 201
    assert schedule(p, version=2).status_code == 409
    assert dating(p, version=2).status_code == 409


@pytest.mark.parametrize("terminal", ["attended", "missed", "cancelled", "rescheduled"])
def test_terminal_appointments_and_their_events_are_preserved(pregnancy, owner, terminal):
    from test_appointments_integration import action, schedule

    p = pregnancy
    row = schedule(p).json["data"]
    if terminal in {"attended", "missed"}:
        row = action(p, row, "confirm").json["data"]
        row = action(
            p, row, "attendance", body={"status": terminal, "effective_at": "2027-06-15T06:00:00Z"}
        ).json["data"]
    elif terminal == "cancelled":
        row = action(p, row, "cancel", body={"reason": "Synthetic prior cancellation"}).json["data"]
    else:
        result = action(
            p,
            row,
            "reschedule",
            body={"reason": "Synthetic prior reschedule", "starts_at": "2027-06-20T06:00:00Z"},
        )
        assert result.status_code == 201
    with owner.connect() as connection:
        before = dict(
            connection.execute(text("SELECT * FROM appointments WHERE id = :id"), {"id": row["id"]})
            .mappings()
            .one()
        )
        events = (
            connection.execute(
                text(
                    "SELECT event_data_cipher FROM appointment_events "
                    "WHERE appointment_id = :id ORDER BY sequence"
                ),
                {"id": row["id"]},
            )
            .scalars()
            .all()
        )
        version = connection.scalar(
            text("SELECT version FROM pregnancy_episodes WHERE id = :id"), {"id": p.episode}
        )
    result = complete(p, version=version)
    assert result.status_code == 201, result.json
    assert result.json["data"]["cancelled_appointment_count"] == (
        1 if terminal == "rescheduled" else 0
    )
    with owner.connect() as connection:
        assert (
            dict(
                connection.execute(
                    text("SELECT * FROM appointments WHERE id = :id"), {"id": row["id"]}
                )
                .mappings()
                .one()
            )
            == before
        )
        assert (
            connection.execute(
                text(
                    "SELECT event_data_cipher FROM appointment_events "
                    "WHERE appointment_id = :id ORDER BY sequence"
                ),
                {"id": row["id"]},
            )
            .scalars()
            .all()
            == events
        )


def test_concurrent_booking_and_completion_share_parent_version_lock(pregnancy, identity, owner):
    from test_appointments_integration import schedule

    p = pregnancy
    second = identity.app.test_client()
    _login(second, "stab.hcw")
    barrier = Barrier(2)

    def close():
        barrier.wait(timeout=10)
        return complete(p, client=second)

    def book():
        barrier.wait(timeout=10)
        return schedule(p)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(close), pool.submit(book)]
        results = [future.result(timeout=30) for future in futures]
    assert sorted(result.status_code for result in results) == [201, 412], [
        row.json for row in results
    ]
    detail = p.client.get(f"{p.path}?facility_id={p.facility}").json["data"]
    if detail["lifecycle"] == "ongoing":
        assert complete(p, version=detail["version"]).status_code == 201
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM appointments WHERE episode_id = :id "
                    "AND status IN ('scheduled','confirmed')"
                ),
                {"id": p.episode},
            )
            == 0
        )


def test_concurrent_confirmation_cannot_leave_future_confirmed_booking(pregnancy, identity, owner):
    from test_appointments_integration import action, schedule

    p = pregnancy
    row = schedule(p).json["data"]
    other = identity.app.test_client()
    _login(other, "stab.hcw")
    barrier = Barrier(2)

    def close():
        barrier.wait(timeout=10)
        return complete(p, client=other, version=2)

    def confirm():
        barrier.wait(timeout=10)
        return action(p, row, "confirm")

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(close), pool.submit(confirm)]
        results = [future.result(timeout=30) for future in futures]
    assert results[0].status_code == 201, results[0].json
    assert results[1].status_code in {200, 412}, results[1].json
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT status FROM appointments WHERE id = :id"), {"id": row["id"]}
            )
            == "cancelled"
        )


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_corrections_do_not_fork_history(pregnancy, identity, owner, same_key):
    p = pregnancy
    first = complete(p).json["data"]
    other = identity.app.test_client()
    _login(other, "stab.hcw")
    barrier = Barrier(2)
    key = str(uuid4())

    def submit(client, request_key):
        barrier.wait(timeout=10)
        return complete(
            p,
            client=client,
            key=request_key,
            version=2,
            supersedes_id=first["id"],
            reason="Synthetic concurrent correction",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(submit, p.client, key),
            pool.submit(submit, other, key if same_key else str(uuid4())),
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
                text("SELECT count(*) FROM pregnancy_completion_records WHERE episode_id = :id"),
                {"id": p.episode},
            )
            == 2
        )


def test_cross_patient_key_reuse_cannot_replay_hidden_completion(pregnancy, owner):
    from types import SimpleNamespace

    p = pregnancy
    key = str(uuid4())
    first = complete(p, key=key)
    assert first.status_code == 201
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
    other_id = registered.json["data"]["pregnancy"]["id"]
    other = SimpleNamespace(
        client=p.client, facility=p.facility, path=f"/api/v1/pregnancy-episodes/{other_id}"
    )
    response = complete(other, key=key)
    assert response.status_code == 409 and first.json["data"]["id"] not in response.get_data(
        as_text=True
    )
    assert (
        p.client.get(f"{other.path}?facility_id={p.facility}").json["data"]["lifecycle"]
        == "ongoing"
    )


def test_mid_batch_cancellation_audit_failure_rolls_back_all_bookings(
    pregnancy, owner, monkeypatch
):
    from test_appointments_integration import schedule

    import tunza.pregnancy.completion as routes

    p = pregnancy
    assert schedule(p).status_code == 201
    assert schedule(p, version=2, starts_at="2027-06-16T06:00:00Z").status_code == 201
    calls = 0

    def fail(*args, **kwargs):
        nonlocal calls
        db.session.flush()
        calls += 1
        if calls == 2:
            raise SQLAlchemyError("Synthetic middle-of-batch audit failure")

    with monkeypatch.context() as patch:
        patch.setattr(routes, "_appointment_audit", fail)
        assert complete(p, version=3).status_code == 503
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM appointments WHERE episode_id = :id "
                    "AND status = 'scheduled'"
                ),
                {"id": p.episode},
            )
            == 2
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM pregnancy_completion_records WHERE episode_id = :id"),
                {"id": p.episode},
            )
            == 0
        )


def test_rls_completion_permission_and_direct_sql_lifecycle_guards(pregnancy, identity, owner):
    from test_auth_integration import _assign_pregnancy_staff

    p = pregnancy
    _assign_pregnancy_staff(identity, p.episode, "tst.vht.a")
    with owner.connect() as connection:
        hcw = connection.scalar(text("SELECT id FROM users WHERE login_identifier = 'stab.hcw'"))
        vht = connection.scalar(text("SELECT id FROM users WHERE login_identifier = 'tst.vht.a'"))
    runtime = create_engine(
        postgres_url(os.environ["TEST_DATABASE_URL"]), pool_size=1, max_overflow=0
    )
    try:
        with (
            pytest.raises(DBAPIError, match="require completion history"),
            runtime.begin() as connection,
        ):
            connection.execute(
                text(
                    "SELECT set_config('tunza.actor_id', :actor, true), "
                    "set_config('tunza.facility_id', :facility, true), "
                    "set_config('tunza.patient_id', :patient, true)"
                ),
                {"actor": str(vht), "facility": p.facility, "patient": p.patient},
            )
            connection.execute(
                text(
                    "UPDATE pregnancy_episodes SET lifecycle = 'completed', "
                    "communication_state = 'completed', version = version + 1 WHERE id = :id"
                ),
                {"id": p.episode},
            )
        result = complete(p)
        assert result.status_code == 201
        record = result.json["data"]
        for user, patient, expected in (
            (hcw, p.patient, 1),
            (vht, p.patient, 0),
            (hcw, str(uuid4()), 0),
            (None, p.patient, 0),
        ):
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
                        "patient": patient,
                    },
                )
                assert (
                    connection.scalar(
                        text(
                            "SELECT count(*) FROM pregnancy_completion_records "
                            "WHERE episode_id = :id"
                        ),
                        {"id": p.episode},
                    )
                    == expected
                )
        for statement in (
            """
                INSERT INTO appointments (id,episode_id,patient_id,facility_id,
                    episode_facility_assignment_id,starts_at,status,created_by)
                VALUES (:id,:episode,:patient,:facility,:assignment,
                        '2027-06-15T06:00:00Z','scheduled',:actor)
            """,
            """
                INSERT INTO pregnancy_dating_revisions (id,episode_id,patient_id,facility_id,
                    basis,estimated_due_date,reason,recorded_by,idempotency_key,request_hash,
                    parent_version,revision_sequence,version)
                VALUES (:id,:episode,:patient,:facility,'synthetic','2027-06-15','Synthetic probe',
                        :actor,:key,:hash,3,1,1)
            """,
        ):
            with (
                pytest.raises(DBAPIError, match="ongoing pregnancy"),
                runtime.begin() as connection,
            ):
                connection.execute(
                    text(
                        "SELECT set_config('tunza.actor_id', :actor, true), "
                        "set_config('tunza.facility_id', :facility, true), "
                        "set_config('tunza.patient_id', :patient, true)"
                    ),
                    {"actor": str(hcw), "facility": p.facility, "patient": p.patient},
                )
                connection.execute(
                    text(statement),
                    {
                        "id": uuid4(),
                        "episode": p.episode,
                        "patient": p.patient,
                        "facility": p.facility,
                        "assignment": record["episode_facility_assignment_id"],
                        "actor": hcw,
                        "key": str(uuid4()),
                        "hash": b"s" * 32,
                    },
                )
        with runtime.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM pregnancy_completion_records")) == 0
    finally:
        runtime.dispose()
