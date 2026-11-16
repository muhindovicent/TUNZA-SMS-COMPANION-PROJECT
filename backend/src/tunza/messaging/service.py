"""Transactional planning and capacity simulation; never authorizes submission."""

from decimal import Decimal
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import select

from tunza.appointments.models import Appointment
from tunza.appointments.routes import _iso
from tunza.extensions import db
from tunza.messaging.models import (
    CostReservation,
    MessageDispatch,
    MessageOccurrence,
    MessageSchedulingEvent,
    MessagingBudget,
    MessagingPolicy,
)
from tunza.patients.models import (
    AdultEligibilityCheck,
    CommunicationPreference,
    ConsentEvent,
    EpisodeFacilityAssignment,
    PregnancyDatingRevision,
)
from tunza.patients.security import establish_evidence_patient_context

CONTROL_LOCK = 59623166001


class Conflict(ValueError):
    pass


def control_lock():
    db.session.execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": CONTROL_LOCK})


def policy(facility):
    return db.session.scalar(
        select(MessagingPolicy)
        .where(MessagingPolicy.facility_id == facility)
        .order_by(MessagingPolicy.revision.desc())
        .limit(1)
    )


def policies_holds(facility):
    reasons = []
    for scope, code in ((None, "global_paused"), (facility, "facility_paused")):
        current = policy(scope)
        if current is None:
            reasons.append("policy_missing")
        elif current.paused:
            reasons.append(code)
    return reasons


def current_budgets(facility, now):
    return db.session.scalars(
        select(MessagingBudget)
        .where(
            sa.or_(MessagingBudget.facility_id.is_(None), MessagingBudget.facility_id == facility),
            MessagingBudget.period_start <= now,
            MessagingBudget.period_end > now,
        )
        .order_by(MessagingBudget.facility_id.nullsfirst(), MessagingBudget.id)
    ).all()


def eligibility(row, episode, now):
    """Fresh facts plus permanent unconfigured-policy holds, never a fixture bypass."""
    establish_evidence_patient_context(episode.patient_id)
    reasons = [
        "D07_unconfigured",
        "D08_unconfigured",
        "D14_unconfigured",
        "D14a_unconfigured",
        "D15_unconfigured",
        "contact_safety_unconfigured",
        "consent_policy_unconfigured",
    ]
    reasons += policies_holds(row.facility_id)
    budgets = current_budgets(row.facility_id, now)
    if not any(b.facility_id is None for b in budgets) or not any(
        b.facility_id == row.facility_id for b in budgets
    ):
        reasons.append("budget_missing")
    preference = db.session.scalar(
        select(CommunicationPreference).where(
            CommunicationPreference.patient_id == row.patient_id,
            CommunicationPreference.facility_id == row.facility_id,
        )
    )
    if preference is None:
        reasons.append("language_unavailable")
    else:
        row.language = preference.language
    consent = db.session.scalar(
        select(ConsentEvent)
        .where(
            ConsentEvent.patient_id == row.patient_id,
            ConsentEvent.facility_id == row.facility_id,
            ConsentEvent.purpose == "antenatal_messaging",
            ConsentEvent.effective_at <= now,
        )
        .order_by(ConsentEvent.event_sequence.desc())
        .limit(1)
    )
    if consent is None:
        reasons.append("consent_absent")
    elif consent.decision == "withdraw":
        reasons.append("consent_withdrawn")
    else:
        # Existing grants/evidence are synthetic; no approved consent is invented.
        reasons.append("consent_unapproved")
    adult = db.session.scalar(
        select(AdultEligibilityCheck)
        .where(
            AdultEligibilityCheck.patient_id == row.patient_id,
            AdultEligibilityCheck.facility_id == row.facility_id,
        )
        .order_by(AdultEligibilityCheck.sequence.desc())
        .limit(1)
    )
    if adult is None or adult.result != "verified":
        reasons.append("adult_eligibility_unverified")
    else:
        reasons.append("adult_eligibility_unapproved")
    if episode.lifecycle != "ongoing":
        reasons.append("episode_completed")
    if episode.communication_state != "active":
        reasons.append("communication_" + episode.communication_state)
    assignment = db.session.get(EpisodeFacilityAssignment, row.assignment_id)
    if (
        assignment is None
        or assignment.valid_from > now
        or (assignment.valid_to is not None and assignment.valid_to <= now)
    ):
        reasons.append("assignment_changed")
    if row.kind == "weekly":
        reasons.append("D05_unconfigured")
        dating = db.session.scalar(
            select(PregnancyDatingRevision.id)
            .where(PregnancyDatingRevision.episode_id == row.episode_id)
            .limit(1)
        )
        if dating is None:
            reasons.append("dating_missing")
    else:
        reasons.append("D06_unconfigured")
        booking = db.session.get(Appointment, row.appointment_id)
        if booking is None or booking.status != "confirmed" or booking.confirmed_at is None:
            reasons.append("appointment_changed")
        elif booking.starts_at <= now:
            reasons.append("appointment_expired")
    selected = (
        db.session.scalar(
            select(
                sa.func.tunza_scheduling_content(
                    row.episode_id,
                    row.facility_id,
                    row.template_id,
                    row.language,
                    "weekly_advice" if row.kind == "weekly" else "appointment_reminder",
                    row.gestational_week,
                )
            )
        )
        if row.template_id
        else None
    )
    reasons.append("content_fixture_only" if selected else "content_unavailable")
    state = "held"
    if "consent_withdrawn" in reasons or episode.communication_state == "withdrawn":
        state = "suppressed"
    if any(
        reason in reasons
        for reason in ("episode_completed", "assignment_changed", "appointment_changed")
    ):
        state = "cancelled"
    if row.expires_at <= now or "appointment_expired" in reasons:
        reasons.append("expired")
        state = "expired"
    return state, sorted(set(reasons)), selected


def event(row, kind, audit):
    db.session.flush()
    recorded = audit("message.plan", "message." + kind, "message_occurrence", row.id, row.state)
    db.session.flush()
    db.session.add(
        MessageSchedulingEvent(
            id=uuid4(),
            occurrence_id=row.id,
            sequence=row.version,
            event_type=kind,
            state=row.state,
            hold_reasons=row.hold_reasons,
            audit_event_id=recorded.id,
        )
    )
    db.session.flush()


def plan(context, episode, facility, body, now, audit):
    establish_evidence_patient_context(episode.patient_id)
    assignment = db.session.scalar(
        select(EpisodeFacilityAssignment).where(
            EpisodeFacilityAssignment.episode_id == episode.id,
            EpisodeFacilityAssignment.facility_id == facility,
            EpisodeFacilityAssignment.valid_from <= now,
            sa.or_(
                EpisodeFacilityAssignment.valid_to.is_(None),
                EpisodeFacilityAssignment.valid_to > now,
            ),
        )
    )
    if assignment is None or episode.lifecycle != "ongoing":
        raise Conflict("Current ongoing responsibility is required")
    if body["kind"] == "appointment_reminder":
        booking = db.session.scalar(
            select(Appointment)
            .where(Appointment.id == body["appointment_id"])
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if booking is None or booking.episode_id != episode.id or booking.facility_id != facility:
            raise Conflict("Confirmed current appointment required")
        if (
            booking.status != "confirmed"
            or booking.confirmed_at is None
            or booking.episode_facility_assignment_id != assignment.id
            or body["expires_at"] > booking.starts_at
        ):
            raise Conflict("Confirmed current appointment and bounded fixture window required")
    query = select(MessageOccurrence).where(
        MessageOccurrence.episode_id == episode.id, MessageOccurrence.kind == body["kind"]
    )
    if body["kind"] == "weekly":
        query = query.where(MessageOccurrence.gestational_week == body["gestational_week"])
    else:
        query = query.where(
            MessageOccurrence.appointment_id == body["appointment_id"],
            MessageOccurrence.reminder_code == body["reminder_code"],
        )
    existing = db.session.scalar(query)
    if existing:
        if any(
            getattr(existing, key) != body[key] for key in ("due_at", "expires_at", "template_id")
        ):
            raise Conflict("Business key already has a different immutable candidate")
        audit(
            "message.plan", "message.duplicate", "message_occurrence", existing.id, existing.state
        )
        return existing, 200
    preference = db.session.scalar(
        select(CommunicationPreference).where(
            CommunicationPreference.patient_id == episode.patient_id,
            CommunicationPreference.facility_id == facility,
        )
    )
    if preference is None:
        raise Conflict("A language preference is required; no fallback is supported")
    row = MessageOccurrence(
        id=uuid4(),
        episode_id=episode.id,
        patient_id=episode.patient_id,
        facility_id=facility,
        assignment_id=assignment.id,
        programme="antenatal",
        language=preference.language,
        version=1,
        checked_at=now,
        **body,
    )
    with db.session.no_autoflush:
        row.state, row.hold_reasons, selected = eligibility(row, episode, now)
    db.session.add(row)
    event(row, "plan", audit)
    preparation(row, selected)
    return row, 201


def preparation(row, selected):
    dispatch = db.session.scalar(
        select(MessageDispatch)
        .where(MessageDispatch.occurrence_id == row.id)
        .execution_options(populate_existing=True)
    )
    dispatch.template_version_id = UUID(selected["id"]) if selected else None
    dispatch.publication_version = selected["generation"] if selected else None
    db.session.flush()


def evaluate(row, episode, now, audit):
    if row.state in {"cancelled", "expired"}:
        audit("message.plan", "message.terminal", "message_occurrence", row.id, row.state)
        return row
    with db.session.no_autoflush:
        state, reasons, selected = eligibility(row, episode, now)
    row.state, row.hold_reasons = state, reasons
    row.version += 1
    event(row, "evaluate", audit)
    preparation(row, selected)
    return row


def reserve(context, facility, key, segments, now, audit_id):
    holds = policies_holds(facility)
    budgets = current_budgets(facility, now)
    if not any(b.facility_id is None for b in budgets) or not any(
        b.facility_id == facility for b in budgets
    ):
        holds.append("budget_missing")
    amounts = []
    for budget in budgets:
        used = db.session.scalar(
            select(sa.func.coalesce(sa.func.sum(CostReservation.amount), 0)).where(
                CostReservation.budget_id == budget.id
            )
        )
        amount = Decimal(segments) * budget.unit_cost
        if used + amount > budget.cap:
            holds.append("budget_exhausted")
        amounts.append((budget, amount))
    if holds:
        return {
            "reserved": False,
            "hold_reasons": sorted(set(holds)),
            "dispatch_eligible": False,
            "synthetic_only": True,
        }
    for budget, amount in amounts:
        db.session.add(
            CostReservation(
                id=uuid4(),
                budget_id=budget.id,
                audit_event_id=audit_id,
                facility_id=facility,
                actor_id=context.user.id,
                key=key,
                segments=segments,
                amount=amount,
                synthetic_only=True,
            )
        )
        db.session.flush()
    return {
        "reserved": True,
        "segments": segments,
        "budget_count": len(amounts),
        "dispatch_eligible": False,
        "synthetic_only": True,
        "currency": "SYN",
    }


def occurrence_data(row):
    dispatch = db.session.scalar(
        select(MessageDispatch).where(MessageDispatch.occurrence_id == row.id)
    )
    return {
        "id": str(row.id),
        "facility_id": str(row.facility_id),
        "episode_id": str(row.episode_id),
        "kind": row.kind,
        "language": row.language,
        "gestational_week": row.gestational_week,
        "appointment_id": str(row.appointment_id) if row.appointment_id else None,
        "state": row.state,
        "hold_reasons": row.hold_reasons,
        "version": row.version,
        "due_at": _iso(row.due_at),
        "expires_at": _iso(row.expires_at),
        "checked_at": _iso(row.checked_at),
        "synthetic_only": True,
        "dispatch_eligible": False,
        "dispatch": {
            "id": str(dispatch.id),
            "state": dispatch.state,
            "template_version_id": str(dispatch.template_version_id)
            if dispatch.template_version_id
            else None,
            "publication_version": dispatch.publication_version,
            "dispatch_eligible": False,
        },
    }
