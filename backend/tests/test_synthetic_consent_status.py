"""Only synthetic event fixtures; no approved legal or clinical policy is implied."""

import pytest

from tunza.patients.consent_status import (
    SyntheticConsentEvent,
    messaging_eligible,
    synthetic_consent_status,
)


def test_grant_is_purpose_scoped_and_requires_evidence_and_notice():
    grant = SyntheticConsentEvent("test-purpose-a", 1, "grant", True, True)
    assert synthetic_consent_status([grant], "test-purpose-a") == "synthetic_grant"
    assert synthetic_consent_status([grant], "test-purpose-b") == "absent"
    assert (
        synthetic_consent_status(
            [SyntheticConsentEvent("test-purpose-a", 1, "grant", True, False)], "test-purpose-a"
        )
        == "unverified"
    )
    assert messaging_eligible() is False


def test_withdrawal_supersedes_grant_and_can_be_followed_by_a_new_synthetic_grant():
    events = [
        SyntheticConsentEvent("test-purpose-a", 2, "withdraw"),
        SyntheticConsentEvent("test-purpose-a", 1, "grant", True, True),
    ]
    assert synthetic_consent_status(events, "test-purpose-a") == "withdrawn"
    events.append(SyntheticConsentEvent("test-purpose-a", 3, "grant", True, True))
    assert synthetic_consent_status(events, "test-purpose-a") == "synthetic_grant"
    assert messaging_eligible() is False


def test_ambiguous_synthetic_event_order_is_rejected():
    events = [
        SyntheticConsentEvent("test-purpose-a", 1, "grant", True, True),
        SyntheticConsentEvent("test-purpose-a", 1, "withdraw"),
    ]
    with pytest.raises(ValueError):
        synthetic_consent_status(events, "test-purpose-a")
