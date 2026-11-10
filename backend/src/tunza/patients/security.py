"""Verified transaction-local patient access context."""

from uuid import UUID

from sqlalchemy import text

from tunza.access import AccessContext
from tunza.extensions import db


def establish_patient_context(context: AccessContext, facility_id: UUID) -> None:
    db.session.execute(
        text(
            "SELECT set_config('tunza.actor_id', :actor_id, true), "
            "set_config('tunza.facility_id', :facility_id, true)"
        ),
        {"actor_id": str(context.user.id), "facility_id": str(facility_id)},
    )
