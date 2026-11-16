"""A15/A16: held work, live invalidation, assignment isolation and synthetic caps."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from test_appointments_integration import action, booking
from test_auth_integration import _csrf, _login
from test_auth_integration import _fresh_rate_limiter as _fresh_rate_limiter
from test_auth_integration import identity as identity
from test_auth_integration import identity_app as identity_app
from test_completion_integration import complete
from test_content_integration import approved, transition
from test_content_integration import clients as clients
from test_content_integration import content_staff as content_staff
from test_pregnancy_integration import care_staff as care_staff
from test_pregnancy_integration import owner as owner
from test_pregnancy_integration import pregnancy as pregnancy

from tunza.config import postgres_url
from tunza.messaging.segments import estimate_segments

pytestmark = pytest.mark.integration
WINDOW = {"due_at": "2027-01-01T06:00:00Z", "expires_at": "2027-01-02T06:00:00Z"}


def actor(identity, login="tst.pa"):
    client = identity.app.test_client()
    _login(client, login)
    return client


def write(client, path, body, key=None, version=None):
    return client.post(
        "/api/v1" + path,
        json=body,
        headers={
            "X-CSRF-Token": _csrf(client),
            "Idempotency-Key": key or str(uuid4()),
            **({"If-Match": f'"{version}"'} if version is not None else {}),
        },
    )


def plan(p, client=None, key=None, **changes):
    return write(
        client or p.client,
        f"/message-occurrences?facility_id={p.facility}",
        {
            "episode_id": p.episode,
            "kind": "weekly",
            "gestational_week": 1,
            **WINDOW,
            **changes,
        },
        key=key,
    )


def detail(p, row):
    rows = p.client.get(f"/api/v1/message-occurrences?facility_id={p.facility}")
    assert rows.status_code == 200, rows.json
    return next(r for r in rows.json["data"] if r["id"] == row["id"])


def pause(client, scope, paused):
    path = f"/api/v1/messaging/{scope}/pause"
    current = client.get(path)
    assert current.status_code == 200, current.json
    result = write(
        client,
        f"/messaging/{scope}/pause",
        {
            "paused": paused,
            "code": "SYN-CONTROL",
            "config": {},
        },
        version=current.json["data"]["version"],
    )
    assert result.status_code == 201, result.json
    return result


def budget(client, scope, cap="10", rate="1"):
    result = write(
        client,
        f"/messaging/{scope}/budgets",
        {
            "period_start": "2020-01-01T00:00:00Z",
            "period_end": "2030-01-01T00:00:00Z",
            "cap": cap,
            "unit_cost": rate,
            "currency": "SYN",
            "synthetic_only": True,
        },
    )
    assert result.status_code == 201, result.json
    return result.json["data"]


def reserve(client, facility, key=None, **changes):
    return write(
        client,
        f"/messaging/facilities/{facility}/capacity-reservations",
        {
            "fixture_encoding": "gsm7",
            "units": 1,
            **changes,
        },
        key=key,
    )


def test_weekly_duplicate_and_replay_have_one_business_key_and_dispatch(pregnancy, owner):
    p = pregnancy
    key = str(uuid4())
    first = plan(p, key=key)
    assert first.status_code == 201, first.json
    row = first.json["data"]
    assert row["state"] == "held" and row["dispatch_eligible"] is False
    assert {
        "D05_unconfigured",
        "D07_unconfigured",
        "D08_unconfigured",
        "D14_unconfigured",
        "D15_unconfigured",
        "consent_absent",
        "contact_safety_unconfigured",
        "policy_missing",
        "budget_missing",
        "dating_missing",
    } <= set(row["hold_reasons"])
    assert plan(p, key=key).json == first.json
    duplicate = plan(p)
    assert duplicate.status_code == 200 and duplicate.json["data"]["id"] == row["id"]
    assert plan(p, due_at="2027-01-01T07:00:00Z").status_code == 409
    with owner.connect() as connection:
        for table in ("message_occurrences", "message_dispatches", "message_scheduling_events"):
            column = "episode_id" if table == "message_occurrences" else "occurrence_id"
            value = p.episode if column == "episode_id" else row["id"]
            assert (
                connection.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE {column}=:id"), {"id": value}
                )
                == 1
            )
        cipher = connection.scalar(
            text("SELECT response_cipher FROM message_commands WHERE key=:key"), {"key": key}
        )
        assert p.episode.encode() not in cipher


def test_concurrent_planning_is_business_idempotent(pregnancy, identity, owner):
    p = pregnancy
    other = actor(identity, "stab.hcw")
    barrier = Barrier(2)

    def run(client):
        barrier.wait(timeout=10)
        return plan(p, client=client)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = [
            f.result(timeout=30) for f in [pool.submit(run, p.client), pool.submit(run, other)]
        ]
    assert sorted(r.status_code for r in responses) == [200, 201], [r.json for r in responses]
    assert responses[0].json["data"]["id"] == responses[1].json["data"]["id"]


def test_reminders_require_confirmation_and_replacement_confirmation(pregnancy):
    p = pregnancy
    original = booking(p)
    request = {
        "kind": "appointment_reminder",
        "gestational_week": None,
        "appointment_id": original["id"],
        "reminder_code": "SYN-REMINDER",
    }
    assert plan(p, **request).status_code == 409
    confirmed = action(p, original, "confirm").json["data"]
    planned = plan(p, **request)
    assert planned.status_code == 201, planned.json
    row = planned.json["data"]
    assert "D06_unconfigured" in row["hold_reasons"]
    replacement = action(
        p,
        confirmed,
        "reschedule",
        body={
            "starts_at": "2027-06-20T06:00:00Z",
            "reason": "Synthetic reschedule",
        },
    ).json["data"]
    assert detail(p, row)["state"] == "cancelled"
    request["appointment_id"] = replacement["id"]
    assert plan(p, **request).status_code == 409
    assert action(p, replacement, "confirm").status_code == 200
    new = plan(p, **request)
    assert new.status_code == 201 and new.json["data"]["id"] != row["id"]


def test_cancelled_appointment_and_completed_episode_invalidate_preparations(pregnancy):
    p = pregnancy
    appt = action(p, booking(p), "confirm").json["data"]
    reminder = plan(
        p,
        kind="appointment_reminder",
        gestational_week=None,
        appointment_id=appt["id"],
        reminder_code="SYN-REMINDER",
    ).json["data"]
    weekly = plan(p).json["data"]
    assert action(p, appt, "cancel", body={"reason": "Synthetic cancellation"}).status_code == 200
    assert detail(p, reminder)["dispatch"]["state"] == "cancelled"
    assert complete(p, version=2).status_code == 201
    assert detail(p, weekly)["state"] == "cancelled"


def test_withdrawal_immediately_suppresses_existing_work_and_preserves_history(pregnancy):
    p = pregnancy
    row = plan(p).json["data"]
    result = write(
        p.client,
        f"/patients/{p.patient}/consent-events?facility_id={p.facility}",
        {
            "purpose": "antenatal_messaging",
            "decision": "withdraw",
            "method": "synthetic",
            "language": "en",
        },
    )
    assert result.status_code == 201, result.json
    assert detail(p, row)["state"] == "suppressed"
    history = p.client.get(
        f"/api/v1/message-occurrences/{row['id']}/events?facility_id={p.facility}"
    )
    assert [e["event_type"] for e in history.json["data"]] == ["plan", "invalidate"]
    assert "consent_absent" in history.json["data"][0]["hold_reasons"]
    assert "consent_withdrawn" in history.json["data"][1]["hold_reasons"]


def test_expired_candidates_never_prepare_ready_work(pregnancy):
    result = plan(pregnancy, due_at="2020-01-01T00:00:00Z", expires_at="2020-01-02T00:00:00Z")
    assert result.status_code == 201, result.json
    assert result.json["data"]["state"] == result.json["data"]["dispatch"]["state"] == "expired"


def test_operations_projection_and_rls_exclude_identity_and_care_actions(
    pregnancy, identity, owner
):
    p = pregnancy
    row = plan(p).json["data"]
    ops = actor(identity)
    result = ops.get("/api/v1/message-occurrences?view=operations")
    assert result.status_code == 200, result.json
    assert p.episode not in result.get_data(as_text=True)
    assert p.patient not in result.get_data(as_text=True)
    assert all(
        set(r)
        <= {
            "id",
            "facility_id",
            "kind",
            "state",
            "hold_reasons",
            "version",
            "due_at",
            "expires_at",
            "synthetic_only",
            "dispatch_eligible",
        }
        for r in result.json["data"]
    )
    assert plan(p, client=ops).status_code == 403
    outsider = actor(identity, "stab.other")
    assert outsider.get(f"/api/v1/message-occurrences?facility_id={p.facility}").json["data"] == []
    denied = plan(p, client=outsider)
    assert denied.status_code == 404
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM audit_events WHERE request_id=:request "
                    "AND outcome='denied' AND permission='message.plan'"
                ),
                {"request": denied.json["error"]["request_id"]},
            )
            == 1
        )
    runtime = create_engine(postgres_url(os.environ["TEST_DATABASE_URL"]))
    try:
        with runtime.begin() as connection:
            assert connection.scalar(text("SELECT count(*) FROM message_occurrences")) == 0
            assert connection.scalar(text("SELECT count(*) FROM message_operations")) == 0
            user = connection.scalar(text("SELECT id FROM users WHERE login_identifier='tst.pa'"))
            connection.execute(
                text("SELECT set_config('tunza.actor_id',:actor,true)"), {"actor": str(user)}
            )
            assert connection.scalar(text("SELECT count(*) FROM message_occurrences")) == 0
            assert connection.scalar(text("SELECT count(*) FROM message_operations")) >= 1
        with pytest.raises(DBAPIError), runtime.begin() as connection:
            connection.execute(
                text("UPDATE message_dispatches SET state='ready' WHERE occurrence_id=:id"),
                {"id": row["id"]},
            )
    finally:
        runtime.dispose()


def test_pause_controls_invalidate_work_and_gate_capacity(pregnancy, identity):
    p = pregnancy
    row = plan(p).json["data"]
    ops = actor(identity)
    pause(ops, "global", True)
    assert "global_paused" in detail(p, row)["hold_reasons"]
    assert reserve(ops, p.facility).json["data"]["reserved"] is False
    pause(ops, "global", False)
    pause(ops, f"facilities/{p.facility}", True)
    assert "facility_paused" in detail(p, row)["hold_reasons"]
    assert reserve(ops, p.facility).json["data"]["reserved"] is False


def test_concurrent_capacity_reservations_enforce_every_cap_and_replay(
    identity, owner, monkeypatch
):
    ops, second = actor(identity), actor(identity)
    facility = str(identity.facility_ids["TST-C"])
    pause(ops, "global", False)
    pause(ops, f"facilities/{facility}", False)
    global_budget = budget(ops, "global", cap="10")
    facility_budget = budget(ops, f"facilities/{facility}", cap="3")
    runtime = create_engine(postgres_url(os.environ["TEST_DATABASE_URL"]))
    try:
        with (
            pytest.raises(DBAPIError, match="Every applicable budget"),
            runtime.begin() as connection,
        ):
            user = connection.scalar(text("SELECT id FROM users WHERE login_identifier='tst.pa'"))
            connection.execute(
                text(
                    "SELECT set_config('tunza.actor_id',:actor,true), "
                    "set_config('tunza.facility_id',:facility,true)"
                ),
                {"actor": str(user), "facility": facility},
            )
            audit_id = uuid4()
            connection.execute(
                text("""
                INSERT INTO audit_events
                  (id,actor_id,facility_id,request_id,permission,action,resource_type,outcome)
                VALUES (:id,:actor,:facility,:request,'messaging.control','messaging.capacity',
                        'capacity_simulation','success')
            """),
                {"id": audit_id, "actor": user, "facility": facility, "request": uuid4()},
            )
            connection.execute(
                text("""
                INSERT INTO cost_reservations
                  (id,budget_id,audit_event_id,facility_id,actor_id,key,segments,amount,synthetic_only)
                VALUES (:id,:budget,:audit,:facility,:actor,:key,1,1,true)
            """),
                {
                    "id": uuid4(),
                    "budget": global_budget["id"],
                    "audit": audit_id,
                    "facility": facility,
                    "actor": user,
                    "key": str(uuid4()),
                },
            )
    finally:
        runtime.dispose()
    # Simulations roll back reservations against every cap if audit/replay persistence fails.
    import tunza.messaging.routes as routes

    def fail(*args, **kwargs):
        raise SQLAlchemyError("Synthetic failure after reservations")

    with monkeypatch.context() as patch:
        patch.setattr(routes, "crypt", fail)
        assert reserve(ops, facility).status_code == 503
    with owner.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM cost_reservations")) == 0
    unicode_result = reserve(ops, facility, fixture_encoding="unicode", units=36)
    assert unicode_result.status_code == 200, unicode_result.json
    assert unicode_result.json["data"]["reserved"] is True
    assert unicode_result.json["data"]["segments"] == 2
    barrier = Barrier(2)
    keys = [str(uuid4()), str(uuid4())]

    def run(client, key):
        barrier.wait(timeout=10)
        return reserve(client, facility, key)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            f.result(timeout=30)
            for f in [pool.submit(run, ops, keys[0]), pool.submit(run, second, keys[1])]
        ]
    assert [r.status_code for r in results] == [200, 200], [r.json for r in results]
    assert sorted(r.json["data"]["reserved"] for r in results) == [False, True]
    for key, result in zip(keys, results, strict=True):
        assert reserve(ops, facility, key).json == result.json
    with owner.connect() as connection:
        for b in (global_budget, facility_budget):
            assert (
                connection.scalar(
                    text("SELECT sum(amount) FROM cost_reservations WHERE budget_id=:id"),
                    {"id": b["id"]},
                )
                == 3
            )
    # A different facility still shares the remaining global allowance.
    other_facility = str(identity.facility_ids["TST-B"])
    pause(ops, f"facilities/{other_facility}", False)
    budget(ops, f"facilities/{other_facility}", cap="100")
    barrier = Barrier(2)

    def global_run(client):
        barrier.wait(timeout=10)
        return reserve(client, other_facility, units=450)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            f.result(timeout=30)
            for f in [pool.submit(global_run, ops), pool.submit(global_run, second)]
        ]
    assert sorted(r.json["data"]["reserved"] for r in results) == [False, True]
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT sum(amount) FROM cost_reservations WHERE budget_id=:id"),
                {"id": global_budget["id"]},
            )
            == 7
        )


@pytest.mark.parametrize(
    "text_value,encoding,units,segments",
    [
        ("A" * 160, "GSM-7", 160, 1),
        ("A" * 161, "GSM-7", 161, 2),
        ("^" * 81, "GSM-7", 162, 2),
        ("^" * 153, "GSM-7", 306, 3),
        ("Ж" * 70, "Unicode", 70, 1),
        ("Ж" * 71, "Unicode", 71, 2),
        ("😀" * 36, "Unicode", 72, 2),
        ("😀" * 67, "Unicode", 134, 3),
    ],
)
def test_gsm_extension_and_unicode_segment_estimates(text_value, encoding, units, segments):
    result = estimate_segments(text_value)
    assert (result.encoding, result.units, result.segments) == (encoding, units, segments)


def test_planning_audit_failure_rolls_back_all_records(pregnancy, owner, monkeypatch):
    import tunza.messaging.routes as routes

    def fail(*args, **kwargs):
        raise SQLAlchemyError("Synthetic audit failure")

    monkeypatch.setattr(routes, "audit", fail)
    response = plan(pregnancy)
    assert response.status_code == 503, response.json
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM message_occurrences WHERE episode_id=:id"),
                {"id": pregnancy.episode},
            )
            == 0
        )


def test_scheduling_events_are_immutable_and_no_activation_even_for_owner(pregnancy, owner):
    row = plan(pregnancy).json["data"]
    with pytest.raises(DBAPIError, match="append-only"), owner.begin() as connection:
        connection.execute(
            text("DELETE FROM message_scheduling_events WHERE occurrence_id=:id"), {"id": row["id"]}
        )
    with pytest.raises(DBAPIError), owner.begin() as connection:
        connection.execute(
            text("UPDATE message_occurrences SET state='active',version=version+1 WHERE id=:id"),
            {"id": row["id"]},
        )


def test_fixture_publication_never_signs_off_and_suspension_invalidates_selection(
    pregnancy, clients
):
    p = pregnancy
    parent, version = approved(clients)
    published = transition(
        clients["reviewer"],
        version,
        "publish",
        {
            "effective_from": "2020-01-01T00:00:00Z",
            "effective_to": "2030-01-01T00:00:00Z",
        },
    )
    assert published.status_code == 200, published.json
    planned = plan(p, template_id=parent["id"])
    assert planned.status_code == 201, planned.json
    row = planned.json["data"]
    assert row["dispatch"]["template_version_id"] == version["id"]
    assert row["dispatch_eligible"] is False and "content_fixture_only" in row["hold_reasons"]
    assert (
        transition(
            clients["reviewer"],
            published.json["data"],
            "suspend",
            {
                "reason": "SYN-SUSPEND",
            },
        ).status_code
        == 200
    )
    current = detail(p, row)
    assert "content_unavailable" in current["hold_reasons"]
    assert current["dispatch"]["template_version_id"] is None
    assert current["dispatch_eligible"] is False


def test_language_changes_are_rechecked_without_fallback(pregnancy, clients, owner):
    parent, version = approved(clients)
    assert (
        transition(
            clients["reviewer"],
            version,
            "publish",
            {
                "effective_from": "2020-01-01T00:00:00Z",
                "effective_to": "2030-01-01T00:00:00Z",
            },
        ).status_code
        == 200
    )
    p = pregnancy
    row = plan(p, template_id=parent["id"]).json["data"]
    with owner.begin() as connection:
        connection.execute(
            text("UPDATE communication_preferences SET language='lg' WHERE patient_id=:id"),
            {"id": p.patient},
        )
    result = write(
        p.client,
        f"/message-occurrences/{row['id']}/evaluate?facility_id={p.facility}",
        {},
        version=row["version"],
    )
    assert result.status_code == 200, result.json
    assert result.json["data"]["language"] == "lg"
    assert result.json["data"]["dispatch"]["template_version_id"] is None


def test_direct_sql_cannot_select_unpublished_content_for_preparation(pregnancy, clients):
    parent, version = approved(clients)
    p = pregnancy
    row = plan(p, template_id=parent["id"]).json["data"]
    runtime = create_engine(postgres_url(os.environ["TEST_DATABASE_URL"]))
    try:
        with (
            pytest.raises(DBAPIError, match="Published fixture reference required"),
            runtime.begin() as connection,
        ):
            user = connection.scalar(text("SELECT id FROM users WHERE login_identifier='stab.hcw'"))
            connection.execute(
                text(
                    "SELECT set_config('tunza.actor_id',:actor,true), "
                    "set_config('tunza.facility_id',:facility,true)"
                ),
                {"actor": str(user), "facility": p.facility},
            )
            connection.execute(
                text(
                    "UPDATE message_dispatches SET template_version_id=:version, "
                    "publication_version=3 WHERE occurrence_id=:id"
                ),
                {"version": version["id"], "id": row["id"]},
            )
    finally:
        runtime.dispose()


def test_malformed_operations_facility_does_not_broaden_scope(identity):
    ops = actor(identity)
    assert (
        ops.get("/api/v1/message-occurrences?view=operations&facility_id=invalid").status_code
        == 422
    )


def test_recheck_requires_current_version_and_does_not_cache_failed_preconditions(pregnancy, owner):
    p = pregnancy
    row = plan(p).json["data"]
    path = f"/message-occurrences/{row['id']}/evaluate?facility_id={p.facility}"
    key = str(uuid4())
    assert write(p.client, path, {}, key=key).status_code == 428
    assert write(p.client, path, {}, key=key, version=0).status_code == 412
    good = write(p.client, path, {}, key=key, version=1)
    assert good.status_code == 200, good.json
    assert good.json["data"]["version"] == 2
    assert write(p.client, path, {}, key=key, version=1).json == good.json
    assert "D07_unconfigured" in good.json["data"]["hold_reasons"]


@pytest.mark.parametrize(
    "changes",
    [
        {"gestational_week": True},
        {"gestational_week": -1},
        {"due_at": "2027-01-01T09:00"},
        {"approved": True},
        {"clinical_signoff": True},
        {"programme": "postnatal"},
        {"template_id": 9},
        {"episode_id": []},
        {"language": "en"},
    ],
)
def test_planning_rejects_fabricated_approval_and_invalid_fixture_fields(pregnancy, changes):
    assert plan(pregnancy, **changes).status_code == 422


def test_completion_audit_failure_rolls_back_scheduling_invalidation(pregnancy, owner, monkeypatch):
    import tunza.pregnancy.completion as completion

    p = pregnancy
    row = plan(p).json["data"]

    def fail(*args, **kwargs):
        raise SQLAlchemyError("Synthetic closure audit failure")

    monkeypatch.setattr(completion, "_record_pregnancy_audit", fail)
    assert complete(p).status_code == 503
    assert detail(p, row)["state"] == "held"
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM message_scheduling_events WHERE occurrence_id=:id"),
                {"id": row["id"]},
            )
            == 1
        )


def test_late_planning_failure_rolls_back_dispatch_projection_history_and_key(
    pregnancy, owner, monkeypatch
):
    import tunza.messaging.routes as routes

    def fail(*args, **kwargs):
        raise SQLAlchemyError("Synthetic replay encryption failure")

    monkeypatch.setattr(routes, "crypt", fail)
    result = plan(pregnancy)
    assert result.status_code == 503, result.json
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM message_occurrences WHERE episode_id=:id"),
                {"id": pregnancy.episode},
            )
            == 0
        )


def test_revoked_assignment_denies_replay_and_history(pregnancy, owner):
    p = pregnancy
    key = str(uuid4())
    row = plan(p, key=key).json["data"]
    with owner.begin() as connection:
        connection.execute(
            text(
                "UPDATE participant_assignments SET valid_to=clock_timestamp() "
                "WHERE episode_facility_assignment_id IN "
                "(SELECT id FROM episode_facility_assignments "
                "WHERE episode_id=:id)"
            ),
            {"id": p.episode},
        )
    assert plan(p, key=key).status_code == 404
    assert (
        p.client.get(
            f"/api/v1/message-occurrences/{row['id']}/events?facility_id={p.facility}"
        ).status_code
        == 404
    )


def test_cursor_is_actor_and_filter_bound(pregnancy, identity):
    p = pregnancy
    assert plan(p).status_code == 201
    assert plan(p, gestational_week=2).status_code == 201
    path = f"/api/v1/message-occurrences?facility_id={p.facility}&limit=1"
    first = p.client.get(path)
    cursor = first.json["page"]["next_cursor"]
    assert cursor
    assert p.client.get(path + "&cursor=" + cursor).status_code == 200
    assert p.client.get(path + "&state=expired&cursor=" + cursor).status_code == 422
    assert actor(identity, "stab.other").get(path + "&cursor=" + cursor).status_code == 422


@pytest.mark.parametrize("operation", ["withdraw", "complete"])
def test_concurrent_restriction_and_planning_cannot_leave_stale_usable_work(
    pregnancy, identity, owner, operation
):
    p = pregnancy
    second = actor(identity, "stab.hcw")
    barrier = Barrier(2)

    def planning():
        barrier.wait(timeout=10)
        return plan(p)

    def restrict():
        barrier.wait(timeout=10)
        if operation == "complete":
            return complete(p, client=second)
        return write(
            second,
            f"/patients/{p.patient}/consent-events?facility_id={p.facility}",
            {
                "purpose": "antenatal_messaging",
                "decision": "withdraw",
                "method": "synthetic",
                "language": "en",
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(planning), pool.submit(restrict)]
        planned, restricted = [f.result(timeout=30) for f in futures]
    assert restricted.status_code == 201, restricted.json
    assert planned.status_code in ({201, 409} if operation == "complete" else {201}), planned.json
    with owner.connect() as connection:
        states = connection.scalars(
            text("SELECT state FROM message_occurrences WHERE episode_id=:id"), {"id": p.episode}
        ).all()
        assert all(
            state == ("cancelled" if operation == "complete" else "suppressed") for state in states
        )
