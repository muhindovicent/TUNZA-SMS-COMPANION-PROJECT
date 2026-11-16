"""Fixture transfers atomically invalidate unsent scheduling without portability."""

from types import SimpleNamespace

import pytest
from sqlalchemy import text
from test_auth_integration import _fresh_rate_limiter as _fresh_rate_limiter
from test_auth_integration import identity as identity
from test_messaging_integration import plan
from test_pregnancy_integration import care_staff as care_staff
from test_pregnancy_integration import owner as owner
from test_pregnancy_integration import pregnancy as pregnancy
from test_transfers_integration import actor, decision, requested
from test_transfers_integration import identity_app as identity_app
from test_transfers_integration import transfer_staff as transfer_staff

pytestmark = pytest.mark.integration


def test_transfer_cancels_old_preparations_preserves_business_key_and_revokes_source(
    pregnancy, identity, owner, transfer_staff
):
    p = pregnancy
    row = plan(p).json["data"]
    request = requested(p, identity)
    result = decision(p, identity, owner, request)
    assert result.status_code == 200, result.json
    assert (
        p.client.get(
            f"/api/v1/message-occurrences/{row['id']}/events?facility_id={p.facility}"
        ).status_code
        == 404
    )
    with owner.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT state FROM message_occurrences WHERE id=:id"), {"id": row["id"]}
            )
            == "cancelled"
        )
        assert (
            connection.scalar(
                text("SELECT state FROM message_dispatches WHERE occurrence_id=:id"),
                {"id": row["id"]},
            )
            == "cancelled"
        )
    target = SimpleNamespace(
        client=actor(identity, "stab.cross"),
        facility=str(identity.facility_ids["TST-B"]),
        episode=p.episode,
    )
    assert plan(target).status_code == 409
    assert plan(target, gestational_week=2).status_code == 201
