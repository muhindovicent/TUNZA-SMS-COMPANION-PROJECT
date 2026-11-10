"""Authenticated encryption for patient fields using externally supplied key material."""

from __future__ import annotations

import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_BYTES = 12


def encrypt_json(value: dict[str, Any], *, key: bytes, key_version: str, purpose: str) -> bytes:
    plaintext = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    nonce = os.urandom(NONCE_BYTES)
    associated_data = f"tunza:{purpose}:{key_version}".encode()
    return nonce + AESGCM(key).encrypt(nonce, plaintext, associated_data)


def decrypt_json(
    ciphertext: bytes, *, key: bytes, key_version: str, purpose: str
) -> dict[str, Any]:
    if len(ciphertext) <= NONCE_BYTES:
        raise ValueError("Invalid encrypted payload.")
    associated_data = f"tunza:{purpose}:{key_version}".encode()
    plaintext = AESGCM(key).decrypt(
        ciphertext[:NONCE_BYTES], ciphertext[NONCE_BYTES:], associated_data
    )
    value = json.loads(plaintext)
    if not isinstance(value, dict):
        raise ValueError("Invalid encrypted payload.")
    return value
