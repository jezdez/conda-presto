"""HMAC-signed solve receipts for drift detection.

A receipt captures the channel state, solver version, and request shape
that produced a solve result.  It is HMAC-signed with a server-side
secret so the same instance (or one sharing the secret) can verify it
later via ``POST /verify``.

See ``docs/proposals/trust/receipt.md`` for the design rationale and
the distinction between receipts (local HMAC) and attestations
(sigstore, CEP-27 aligned).
"""

from __future__ import annotations

import base64
import hashlib
import hmac

import msgspec

from .cache import canonical_request_hash


class ChannelSnapshot(msgspec.Struct, frozen=True):
    url: str
    subdir: str
    repodata_sha256: str
    fetched_at: str


class Receipt(msgspec.Struct, frozen=True):
    v: int
    request_hash: str
    channels: list[ChannelSnapshot]
    solver_name: str
    solver_version: str
    presto_version: str
    solved_at: str


class VerifyResult(msgspec.Struct):
    verified: bool
    receipt_age_seconds: float
    channel_state_drift: bool
    drift: dict | None = None


def request_hash(
    specs: list[str],
    channels: list[str],
    platforms: list[str] | None,
    format_name: str | None,
) -> str:
    """Deterministic hash of the solve request parameters.

    Reuses :func:`~conda_presto.cache.canonical_request_hash` for
    consistency between receipt hashes and permalink keys.
    """
    return canonical_request_hash(specs, channels, platforms, format_name)


def encode_receipt(receipt: Receipt, secret: bytes) -> str:
    """Encode a receipt as a base64 HMAC-signed blob."""
    payload = msgspec.json.encode(receipt)
    sig = hmac.new(secret, payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload + sig).decode("ascii")


def decode_receipt(encoded: str, secret: bytes) -> Receipt:
    """Decode and verify a base64 HMAC-signed receipt.

    Raises ``ValueError`` if the signature is invalid or the format
    is unrecognized.
    """
    try:
        raw = base64.urlsafe_b64decode(encoded)
    except Exception as exc:
        raise ValueError(f"Invalid receipt encoding: {exc}") from exc

    if len(raw) < 32:
        raise ValueError("Receipt too short")

    payload = raw[:-32]
    sig = raw[-32:]

    expected = hmac.new(secret, payload, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        raise ValueError("Receipt signature invalid")

    receipt = msgspec.json.decode(payload, type=Receipt)
    if receipt.v != 1:
        raise ValueError(f"Unsupported receipt version: {receipt.v}")

    return receipt
