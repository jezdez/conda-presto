"""Tests for conda_presto.receipt (HMAC-signed solve receipts)."""

from __future__ import annotations

import base64

import msgspec
import pytest

from conda_presto.receipt import (
    Receipt,
    VerifyResult,
    _request_hash,
    decode_receipt,
    encode_receipt,
)


@pytest.fixture()
def secret():
    return b"test-secret-key-32-bytes-long!!"


@pytest.fixture()
def sample_receipt():
    return Receipt(
        v=1,
        request_hash="abc123",
        channels=[],
        solver_name="rattler",
        solver_version="1.0.0",
        presto_version="0.1.0",
        solved_at="2026-05-08T12:00:00+00:00",
    )


def test_encode_decode_round_trip(secret, sample_receipt):
    encoded = encode_receipt(sample_receipt, secret)
    decoded = decode_receipt(encoded, secret)
    assert decoded == sample_receipt


def test_wrong_secret_raises(secret, sample_receipt):
    encoded = encode_receipt(sample_receipt, secret)
    with pytest.raises(ValueError, match="signature invalid"):
        decode_receipt(encoded, b"wrong-secret-key-32-bytes-long!")


def test_tampered_payload_raises(secret, sample_receipt):
    encoded = encode_receipt(sample_receipt, secret)
    raw = base64.urlsafe_b64decode(encoded)
    tampered = bytearray(raw)
    tampered[5] ^= 0xFF
    tampered_encoded = base64.urlsafe_b64encode(bytes(tampered)).decode("ascii")
    with pytest.raises(ValueError, match="signature invalid"):
        decode_receipt(tampered_encoded, secret)


def test_unsupported_version_raises(secret):
    receipt_v2 = Receipt(
        v=2,
        request_hash="abc",
        channels=[],
        solver_name="rattler",
        solver_version="1.0.0",
        presto_version="0.1.0",
        solved_at="2026-05-08T12:00:00+00:00",
    )
    encoded = encode_receipt(receipt_v2, secret)
    with pytest.raises(ValueError, match="Unsupported receipt version"):
        decode_receipt(encoded, secret)


def test_invalid_base64_raises(secret):
    with pytest.raises(
        ValueError, match="(Invalid receipt encoding|Receipt too short)"
    ):
        decode_receipt("not-valid-base64!!!", secret)


def test_too_short_payload_raises(secret):
    short = base64.urlsafe_b64encode(b"short").decode("ascii")
    with pytest.raises(ValueError, match="Receipt too short"):
        decode_receipt(short, secret)


def test_request_hash_deterministic_regardless_of_order():
    h1 = _request_hash(["numpy", "python"], ["conda-forge"], ["linux-64"], None)
    h2 = _request_hash(["python", "numpy"], ["conda-forge"], ["linux-64"], None)
    assert h1 == h2


def test_request_hash_differs_for_different_specs():
    h1 = _request_hash(["numpy"], ["conda-forge"], ["linux-64"], None)
    h2 = _request_hash(["pandas"], ["conda-forge"], ["linux-64"], None)
    assert h1 != h2


def test_verify_result_serializes():
    result = VerifyResult(
        verified=True,
        receipt_age_seconds=42.5,
        channel_state_drift=False,
        drift=None,
    )
    data = msgspec.json.decode(msgspec.json.encode(result))
    assert data["verified"] is True
    assert data["receipt_age_seconds"] == 42.5
    assert data["channel_state_drift"] is False
    assert data["drift"] is None
