from tunza.identity.passwords import (
    discard_password_verification,
    hash_password,
    verify_password,
)


def test_hashed_password_verifies_and_rejects_mismatches():
    digest = hash_password("synthetic-correct-password")
    assert digest != "synthetic-correct-password"
    assert digest.startswith("$argon2id$")
    assert verify_password(digest, "synthetic-correct-password") is True
    assert verify_password(digest, "synthetic-wrong-password") is False


def test_malformed_hash_rejects_without_leaking_details():
    assert verify_password("not-a-real-hash", "synthetic-password") is False


def test_dummy_verification_always_fails_but_consumes_work():
    digest = hash_password("another-synthetic-password")
    discard_password_verification("anything")
    discard_password_verification(digest)
    assert verify_password(digest, "anything") is False
