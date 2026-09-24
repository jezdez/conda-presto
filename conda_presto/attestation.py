"""Signing and verification of exact service output bytes.

The standard in-toto Link records an output-signing step, not solve provenance.
Sigstore's public signing API is used directly because conda-sigstore 0.1.2's
generic signer falls back to interactive login when ambient credentials are absent.
"""

from __future__ import annotations

import hashlib
import logging
import multiprocessing
import os
import time
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
MAX_BUNDLE_BYTES = 10 * 1024 * 1024
LINK_PREDICATE_TYPE = "https://in-toto.io/attestation/link/v0.3"
SIGNING_STEP = "conda-presto-sign"


class AttestationError(ValueError):
    """An attestation failure with a safe message for HTTP clients."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class AttestationService:
    """Use configured Sigstore trust and require unattended signing credentials."""

    trust_config: Path | None = None
    allow_public_signing: bool = False
    offline: bool = False

    def run_until(
        self,
        operation: Literal["sign", "verify"],
        *,
        deadline: float,
        **kwargs,
    ) -> str | dict[str, object]:
        """Run provider work in an isolated process with a bounded lifetime."""
        if operation not in {"sign", "verify"}:
            raise AttestationError("invalid-operation", "Unsupported operation")
        if deadline <= time.monotonic():
            raise TimeoutError
        process_context = multiprocessing.get_context("spawn")
        receiver, sender = process_context.Pipe(duplex=False)
        process = process_context.Process(
            target=self._run_process, args=(sender, operation, kwargs)
        )
        try:
            process.start()
            sender.close()
            if not receiver.poll(max(0.0, deadline - time.monotonic())):
                raise TimeoutError
            try:
                status, payload = receiver.recv()
            except EOFError:
                raise AttestationError(
                    "operation-failed", "Attestation worker exited without a result"
                ) from None
        finally:
            receiver.close()
            sender.close()
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                    process.join(max(0.0, min(5.0, deadline - time.monotonic())))
                if process.is_alive():
                    process.kill()
                process.join(5)
                if process.is_alive():
                    raise AttestationError(
                        "operation-failed", "Attestation worker did not exit"
                    )
                process.close()
        if status == "ok":
            return payload
        raise AttestationError(*payload)

    def _run_process(self, sender, operation: str, kwargs: dict[str, object]) -> None:
        """Return only results and sanitized errors from the provider process."""
        logging.disable(logging.CRITICAL)
        try:
            with (
                open(os.devnull, "w") as output,
                redirect_stdout(output),
                redirect_stderr(output),
            ):
                result = getattr(self, operation)(**kwargs)
            sender.send(("ok", result))
        except AttestationError as exc:
            sender.send(("error", (exc.code, str(exc))))
        except Exception:
            sender.send(("error", ("operation-failed", "Attestation operation failed")))
        finally:
            sender.close()

    @staticmethod
    def available() -> bool:
        """Check the provider API without loading trust or credentials."""
        try:
            from conda_sigstore.statements import InTotoStatement
            from conda_sigstore.verification import SigstoreVerifier
            from sigstore.sign import SigningContext
        except ImportError:
            return False
        return all(
            (
                callable(getattr(InTotoStatement, "from_payload", None)),
                callable(getattr(SigstoreVerifier, "verify_statement", None)),
                callable(getattr(SigningContext, "from_trust_config", None)),
            )
        )

    @staticmethod
    def validate_artifact(body: bytes, artifact_name: str) -> str:
        """Validate supplied bytes and return their SHA256 digest."""
        if not isinstance(body, bytes):
            raise AttestationError("invalid-artifact", "Artifact must be bytes")
        if len(body) > MAX_ARTIFACT_BYTES:
            raise AttestationError("artifact-too-large", "Artifact exceeds size limit")
        if (
            not isinstance(artifact_name, str)
            or not artifact_name.strip()
            or len(artifact_name) > 1024
            or "\0" in artifact_name
        ):
            raise AttestationError("invalid-artifact", "Artifact name is invalid")
        return hashlib.sha256(body).hexdigest()

    @staticmethod
    def signing_predicate(subject: dict[str, object]) -> dict[str, object]:
        """Describe signing an existing output without changing its bytes."""
        return {
            "name": SIGNING_STEP,
            "command": [],
            "materials": [subject],
            "byproducts": {},
            "environment": {},
        }

    def sign(self, body: bytes, *, artifact_name: str) -> str:
        """Sign retained service output supplied by the result handler."""
        if self.trust_config is None and not self.allow_public_signing:
            raise AttestationError(
                "signing-disabled", "Signing requires an operator trust configuration"
            )
        if self.offline:
            raise AttestationError(
                "signing-unavailable", "Signing is unavailable in offline mode"
            )
        digest = self.validate_artifact(body, artifact_name)
        try:
            from conda_sigstore.settings import MAX_TRUST_CONFIG_BYTES
            from conda_sigstore.statements import InTotoStatement
            from conda_sigstore.transport import read_bounded_file
            from conda_sigstore.verification import SigstoreVerifier
            from sigstore.dsse import Statement
            from sigstore.models import ClientTrustConfig
            from sigstore.oidc import IdentityToken, detect_credential
            from sigstore.sign import SigningContext
            from sigstore.verify import Verifier
        except ImportError:
            raise AttestationError(
                "provider-unavailable", "Sigstore provider API is unavailable"
            ) from None

        try:
            raw_token = detect_credential()
            if not raw_token:
                raise AttestationError(
                    "signing-credentials-unavailable",
                    "Noninteractive signing credentials are unavailable",
                )
            token = IdentityToken(raw_token)
            trust = (
                ClientTrustConfig.from_json(
                    read_bounded_file(
                        self.trust_config,
                        MAX_TRUST_CONFIG_BYTES,
                        description="trust configuration",
                    ).decode("utf-8")
                )
                if self.trust_config is not None
                else ClientTrustConfig.production()
            )
            subject = {"name": artifact_name, "digest": {"sha256": digest}}
            statement = InTotoStatement.from_payload(
                {
                    "_type": InTotoStatement.STATEMENT_TYPE,
                    "subject": [subject],
                    "predicateType": LINK_PREDICATE_TYPE,
                    "predicate": self.signing_predicate(subject),
                }
            )
            payload = statement.payload()
            signing = SigningContext.from_trust_config(trust)
            with signing.signer(token) as signer:
                bundle_json = signer.sign_dsse(Statement(payload)).to_json()
            if len(bundle_json.encode("utf-8")) > MAX_BUNDLE_BYTES:
                raise AttestationError(
                    "bundle-too-large", "Signed bundle exceeds size limit"
                )
            verified = SigstoreVerifier(
                verifier=Verifier(trusted_root=trust.trusted_root)
            ).verify_statement(bundle_json)
            if verified.payload != payload:
                raise AttestationError(
                    "signing-failed",
                    "Signed bundle does not match the output statement",
                )
            return bundle_json
        except AttestationError:
            raise
        except Exception:
            raise AttestationError("signing-failed", "Output signing failed") from None

    def verify(
        self,
        body: bytes,
        bundle_json: str,
        *,
        artifact_name: str,
        expected_identity: str,
        expected_issuer: str,
    ) -> dict[str, object]:
        """Check signature, subject and signer, with optional Link shape checks.

        ``claims_checked`` reports signing-step shape and material consistency.
        It does not establish solve provenance or independently prove other claims.
        """
        digest = self.validate_artifact(body, artifact_name)
        if not all(
            isinstance(value, str) and value.strip() and len(value) <= 2048
            for value in (expected_identity, expected_issuer)
        ):
            raise AttestationError(
                "invalid-identity", "Expected signer identity and issuer are required"
            )
        if not isinstance(bundle_json, str):
            raise AttestationError("invalid-bundle", "Bundle must be JSON text")
        try:
            if (
                len(bundle_json) > MAX_BUNDLE_BYTES
                or len(bundle_json.encode("utf-8")) > MAX_BUNDLE_BYTES
            ):
                raise AttestationError("bundle-too-large", "Bundle exceeds size limit")
        except UnicodeError:
            raise AttestationError("invalid-bundle", "Bundle must be UTF-8") from None

        try:
            from conda_sigstore.exceptions import (
                BundleVerificationError,
                StatementError,
                TrustMaterialUnavailableError,
            )
            from conda_sigstore.verification import SigstoreVerifier
        except ImportError:
            raise AttestationError(
                "provider-unavailable", "Sigstore provider API is unavailable"
            ) from None

        try:
            verified = SigstoreVerifier.shared(
                offline=self.offline, trust_config=self.trust_config
            ).verify_statement(bundle_json)
            subjects = verified.statement.subjects()
        except TrustMaterialUnavailableError:
            raise AttestationError(
                "evidence-unavailable", "Sigstore trust material is unavailable"
            ) from None
        except BundleVerificationError:
            raise AttestationError(
                "invalid-bundle", "Sigstore bundle verification failed"
            ) from None
        except StatementError:
            raise AttestationError(
                "invalid-statement", "Bundle contains an invalid in-toto statement"
            ) from None
        except Exception:
            raise AttestationError(
                "verification-failed", "Attestation verification failed"
            ) from None

        if len(subjects) != 1:
            raise AttestationError(
                "unsupported-subjects", "Verification requires exactly one subject"
            )
        subject = subjects[0]
        if set(subject.digest) != {"sha256"}:
            raise AttestationError(
                "unsupported-digests", "Verification requires exactly a SHA256 digest"
            )
        if subject.name != artifact_name or subject.digest.get("sha256") != digest:
            raise AttestationError(
                "artifact-mismatch", "Statement subject does not match the artifact"
            )
        if (
            verified.signer.identity != expected_identity
            or verified.signer.issuer != expected_issuer
        ):
            raise AttestationError(
                "untrusted-identity", "Signer identity or issuer does not match"
            )

        predicate_type = verified.statement.predicate_type
        predicate = verified.statement.value.get("predicate")
        claims_checked = False
        if (
            predicate_type == LINK_PREDICATE_TYPE
            and isinstance(predicate, dict)
            and predicate.get("name") == SIGNING_STEP
        ):
            if predicate != self.signing_predicate(subject.to_dict()):
                raise AttestationError(
                    "invalid-predicate",
                    "Output-signing step does not match the artifact",
                )
            claims_checked = True

        return {
            "signature_verified": True,
            "artifact_verified": True,
            "signer_verified": True,
            "claims_checked": claims_checked,
            "artifact_name": artifact_name,
            "artifact_sha256": digest,
            "identity": verified.signer.identity,
            "issuer": verified.signer.issuer,
            "predicate_type": predicate_type,
        }
