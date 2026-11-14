"""Fail-closed status helpers for synthetic consent test scenarios.

These helpers do not constitute an approved eligibility or consent policy.
"""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class SyntheticConsentEvent:
    """Already-validated synthetic event, ordered by persisted sequence."""

    purpose: str
    sequence: int
    decision: Literal["grant", "withdraw"]
    has_notice: bool = False
    has_evidence: bool = False


def synthetic_consent_status(events: list[SyntheticConsentEvent], purpose: str) -> str:
    """Return a test-only status; a withdrawal supersedes an earlier grant."""
    applicable = [event for event in events if event.purpose == purpose]
    if not applicable:
        return "absent"
    if any(event.sequence <= 0 for event in applicable) or len(
        {event.sequence for event in applicable}
    ) != len(applicable):
        raise ValueError("Synthetic event sequences must be unique positive integers.")
    latest = max(applicable, key=lambda event: event.sequence)
    if latest.decision == "withdraw":
        return "withdrawn"
    if latest.decision != "grant" or not latest.has_notice or not latest.has_evidence:
        return "unverified"
    return "synthetic_grant"


def messaging_eligible() -> bool:
    """D02 and D03 are unresolved: no test state authorizes actual messaging."""
    return False
