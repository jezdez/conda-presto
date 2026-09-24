"""Verify the public fixture against an independently chosen test policy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from conda_presto.attestation import AttestationError, AttestationService

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("artifact", type=Path)
parser.add_argument("--identity")
parser.add_argument(
    "--expect-error", choices=("artifact-mismatch", "untrusted-identity")
)
args = parser.parse_args()
policy = json.loads(Path("policy.json").read_text())
if args.identity is not None:
    policy["expected_identity"] = args.identity

service = AttestationService(offline=True, trust_config=Path("trust.json"))
try:
    result = service.verify(
        args.artifact.read_bytes(),
        Path("bundle.sigstore.json").read_text(),
        **policy,
    )
except AttestationError as exc:
    if exc.code != args.expect_error:
        raise
    print(f"Rejected: {exc.code}")
else:
    if args.expect_error:
        raise SystemExit(f"Expected {args.expect_error}, but verification succeeded")
    assert result["signature_verified"]
    assert result["artifact_verified"]
    assert result["signer_verified"]
    assert result["claims_checked"] is False
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "signature_verified",
                    "artifact_verified",
                    "signer_verified",
                    "claims_checked",
                )
            },
            indent=2,
        )
    )
