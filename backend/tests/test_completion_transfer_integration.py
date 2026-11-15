"""Completion/fixture-transfer races retain one authorized lifecycle owner."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from test_auth_integration import _fresh_rate_limiter as _fresh_rate_limiter
from test_auth_integration import identity as identity
from test_completion_integration import complete
from test_pregnancy_integration import care_staff as care_staff
from test_pregnancy_integration import owner as owner
from test_pregnancy_integration import pregnancy as pregnancy
from test_transfers_integration import actor, decision, requested
from test_transfers_integration import identity_app as identity_app
from test_transfers_integration import transfer_staff as transfer_staff

pytestmark = pytest.mark.integration


def test_completed_episode_cannot_accept_pending_transfer(
    pregnancy, identity, owner, transfer_staff
):
    p = pregnancy
    request = requested(p, identity)
    assert complete(p, version=2).status_code == 201
    result = decision(p, identity, owner, request)
    assert result.status_code == 409, result.json
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT state FROM facility_transfers WHERE id = :id"), {"id": request["id"]}
            )
            == "pending"
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM episode_facility_assignments WHERE episode_id = :id"),
                {"id": p.episode},
            )
            == 1
        )


def test_completion_and_acceptance_are_serialized_and_reauthorize_source(
    pregnancy, identity, owner, transfer_staff
):
    p = pregnancy
    request = requested(p, identity)
    receiver = actor(identity, "transfer.fa.b")
    barrier = Barrier(2)

    def close():
        barrier.wait(timeout=10)
        return complete(p, version=2)

    def accept():
        barrier.wait(timeout=10)
        return decision(p, identity, owner, request, client=receiver)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(close), pool.submit(accept)]
        closed, accepted = [future.result(timeout=30) for future in futures]
    assert (closed.status_code, accepted.status_code) in {(201, 409), (404, 200)}, [
        closed.json,
        accepted.json,
    ]
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
        lifecycle = connection.scalar(
            text("SELECT lifecycle FROM pregnancy_episodes WHERE id = :id"), {"id": p.episode}
        )
        assert lifecycle == ("completed" if closed.status_code == 201 else "ongoing")


def test_completion_after_transfer_preserves_source_owned_appointment_history(
    pregnancy, identity, owner, transfer_staff
):
    from test_appointments_integration import schedule

    p = pregnancy
    source_booking = schedule(p).json["data"]
    request = requested(p, identity)
    assert decision(p, identity, owner, request).status_code == 200
    care = actor(identity, "stab.cross")
    destination = str(identity.facility_ids["TST-B"])
    target = SimpleNamespace(client=care, facility=destination, path=p.path)
    version = care.get(f"{p.path}?facility_id={destination}").json["data"]["version"]
    booked = schedule(target, version=version)
    assert booked.status_code == 201, booked.json
    result = complete(target, version=version + 1)
    assert result.status_code == 201 and result.json["data"]["cancelled_appointment_count"] == 1
    assert p.client.get(f"{p.path}/completion?facility_id={p.facility}").status_code == 404
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT status FROM appointments WHERE id = :id"), {"id": source_booking["id"]}
            )
            == "scheduled"
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM appointment_events WHERE appointment_id = :id"),
                {"id": source_booking["id"]},
            )
            == 1
        )
        assert (
            connection.scalar(
                text("SELECT status FROM appointments WHERE id = :id"),
                {"id": booked.json["data"]["id"]},
            )
            == "cancelled"
        )
