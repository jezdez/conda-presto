from __future__ import annotations

import hashlib
import json
import socket
import sys
import time
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

import conda_presto.attestation as attestation_module
from conda_presto.attestation import (
    LINK_PREDICATE_TYPE,
    SIGNING_STEP,
    AttestationError,
    AttestationService,
)

ARTIFACT = b'{"packages":[]}\n'
ARTIFACT_NAME = "result.json"
IDENTITY = "https://example.org/workflow"
ISSUER = "https://example.org/issuer"


@pytest.fixture
def sigstore_provider(monkeypatch):
    state = SimpleNamespace(
        statement={
            "_type": "https://in-toto.io/Statement/v1",
            "subject": [
                {
                    "name": ARTIFACT_NAME,
                    "digest": {"sha256": hashlib.sha256(ARTIFACT).hexdigest()},
                }
            ],
            "predicateType": "https://example.org/unknown",
            "predicate": {"arbitrary_claim": True},
        },
        signer=SimpleNamespace(identity=IDENTITY, issuer=ISSUER),
        verification_error=None,
        payload_override=None,
        token="ambient-credential",
        credential_calls=0,
        public_trust_calls=0,
        private_trust_reads=[],
        signing_calls=0,
        verifier_options=[],
    )
    errors = SimpleNamespace(
        BundleVerificationError=type("BundleVerificationError", (RuntimeError,), {}),
        StatementError=type("StatementError", (ValueError,), {}),
    )
    errors.TrustMaterialUnavailableError = type(
        "TrustMaterialUnavailableError", (errors.BundleVerificationError,), {}
    )
    state.errors = errors

    def statement_from_payload(value):
        def subjects():
            return tuple(
                SimpleNamespace(
                    name=subject["name"],
                    digest=subject["digest"],
                    to_dict=lambda subject=subject: subject,
                )
                for subject in value["subject"]
            )

        return SimpleNamespace(
            value=value,
            predicate_type=value["predicateType"],
            subjects=subjects,
            payload=lambda: json.dumps(value, sort_keys=True).encode(),
        )

    def verify_statement(bundle_json):
        if state.verification_error:
            raise state.verification_error
        statement = statement_from_payload(state.statement)
        return SimpleNamespace(
            statement=statement,
            signer=state.signer,
            payload=state.payload_override or statement.payload(),
        )

    def verifier(**kwargs):
        state.verifier_options.append(kwargs)
        return SimpleNamespace(verify_statement=verify_statement)

    verifier.shared = verifier
    verifier.verify_statement = verify_statement

    def detect_credential():
        state.credential_calls += 1
        return state.token

    def sign_dsse(statement):
        state.signing_calls += 1
        state.statement = json.loads(statement)
        return SimpleNamespace(to_json=lambda: "signed-bundle")

    def public_trust():
        state.public_trust_calls += 1
        return SimpleNamespace(trusted_root="test-root")

    def read_trust(path, limit, **kwargs):
        state.private_trust_reads.append((path, limit))
        return b'{"configured": true}'

    def interactive_login(*args, **kwargs):
        pytest.fail("Signing must never start interactive login")

    modules = {
        "conda_sigstore": {},
        "conda_sigstore.exceptions": vars(errors),
        "conda_sigstore.settings": {"MAX_TRUST_CONFIG_BYTES": 1000},
        "conda_sigstore.transport": {"read_bounded_file": read_trust},
        "conda_sigstore.statements": {
            "InTotoStatement": SimpleNamespace(
                STATEMENT_TYPE="https://in-toto.io/Statement/v1",
                from_payload=statement_from_payload,
            )
        },
        "conda_sigstore.verification": {"SigstoreVerifier": verifier},
        "sigstore": {},
        "sigstore.dsse": {"Statement": lambda payload: payload},
        "sigstore.models": {
            "ClientTrustConfig": SimpleNamespace(
                production=public_trust,
                from_json=lambda value: SimpleNamespace(trusted_root="private-root"),
            )
        },
        "sigstore.oidc": {
            "IdentityToken": lambda token: token,
            "detect_credential": detect_credential,
            "Issuer": interactive_login,
        },
        "sigstore.sign": {
            "SigningContext": SimpleNamespace(
                from_trust_config=lambda trust: SimpleNamespace(
                    signer=lambda token: nullcontext(
                        SimpleNamespace(sign_dsse=sign_dsse)
                    )
                )
            )
        },
        "sigstore.verify": {"Verifier": lambda **kwargs: kwargs},
    }
    for name, members in modules.items():
        module = ModuleType(name)
        vars(module).update(members)
        monkeypatch.setitem(sys.modules, name, module)
    return state


@pytest.fixture
def verify_output(sigstore_provider):
    def verify(**overrides):
        kwargs = {
            "body": ARTIFACT,
            "bundle_json": "test-bundle",
            "artifact_name": ARTIFACT_NAME,
            "expected_identity": IDENTITY,
            "expected_issuer": ISSUER,
        }
        kwargs.update(overrides)
        return AttestationService().verify(**kwargs)

    return verify


def test_verify_distinguishes_unknown_claims_from_signature_and_artifact(verify_output):
    result = verify_output()

    assert result["signature_verified"] is True
    assert result["artifact_verified"] is True
    assert result["signer_verified"] is True
    assert result["claims_checked"] is False
    assert result["artifact_sha256"] == hashlib.sha256(ARTIFACT).hexdigest()


@pytest.mark.parametrize(
    "overrides, code",
    [
        ({"body": ARTIFACT.rstrip()}, "artifact-mismatch"),
        ({"artifact_name": "different.json"}, "artifact-mismatch"),
        ({"expected_identity": "other"}, "untrusted-identity"),
        ({"expected_issuer": "other"}, "untrusted-identity"),
        ({"expected_identity": ""}, "invalid-identity"),
        ({"expected_issuer": ""}, "invalid-identity"),
    ],
)
def test_verify_rejects_changed_output_or_wrong_recipient_expectation(
    verify_output, overrides, code
):
    with pytest.raises(AttestationError) as exc:
        verify_output(**overrides)
    assert exc.value.code == code


def test_verify_does_not_partially_accept_multiple_subjects(
    sigstore_provider, verify_output
):
    sigstore_provider.statement["subject"] *= 2
    with pytest.raises(AttestationError) as exc:
        verify_output()
    assert exc.value.code == "unsupported-subjects"


def test_verify_does_not_accept_unchecked_digest_algorithms(
    sigstore_provider, verify_output
):
    sigstore_provider.statement["subject"][0]["digest"]["sha512"] = "unchecked"
    with pytest.raises(AttestationError) as exc:
        verify_output()
    assert exc.value.code == "unsupported-digests"


@pytest.mark.parametrize("bad_material", [False, True])
def test_verify_checks_the_known_signing_step(
    sigstore_provider, verify_output, bad_material
):
    statement = sigstore_provider.statement
    statement["predicateType"] = LINK_PREDICATE_TYPE
    statement["predicate"] = AttestationService.signing_predicate(
        statement["subject"][0]
    )
    if bad_material:
        statement["predicate"]["materials"] = []
        with pytest.raises(AttestationError) as exc:
            verify_output()
        assert exc.value.code == "invalid-predicate"
    else:
        assert verify_output()["claims_checked"] is True


@pytest.mark.parametrize(
    "error_name, code",
    [
        ("BundleVerificationError", "invalid-bundle"),
        ("TrustMaterialUnavailableError", "evidence-unavailable"),
        ("StatementError", "invalid-statement"),
        (None, "verification-failed"),
    ],
)
def test_verify_sanitizes_provider_failures(
    sigstore_provider, verify_output, error_name, code
):
    error_type = (
        getattr(sigstore_provider.errors, error_name) if error_name else RuntimeError
    )
    sigstore_provider.verification_error = error_type("secret-provider-detail")
    with pytest.raises(AttestationError) as exc:
        verify_output()
    assert exc.value.code == code
    assert "secret-provider-detail" not in str(exc.value)


def test_verify_enforces_byte_limits_before_provider_use(monkeypatch, verify_output):
    monkeypatch.setattr(attestation_module, "MAX_BUNDLE_BYTES", 3)
    with pytest.raises(AttestationError) as exc:
        verify_output(bundle_json="éé")
    assert exc.value.code == "bundle-too-large"

    monkeypatch.setattr(attestation_module, "MAX_ARTIFACT_BYTES", 1)
    with pytest.raises(AttestationError) as exc:
        verify_output()
    assert exc.value.code == "artifact-too-large"


def test_sign_requires_operator_opt_in(sigstore_provider):
    with pytest.raises(AttestationError) as exc:
        AttestationService().sign(ARTIFACT, artifact_name=ARTIFACT_NAME)
    assert exc.value.code == "signing-disabled"
    assert sigstore_provider.credential_calls == 0


def test_sign_rejects_offline_mode_before_discovering_credentials(sigstore_provider):
    with pytest.raises(AttestationError) as exc:
        AttestationService(allow_public_signing=True, offline=True).sign(
            ARTIFACT, artifact_name=ARTIFACT_NAME
        )
    assert exc.value.code == "signing-unavailable"
    assert sigstore_provider.credential_calls == 0


def test_sign_never_falls_back_to_interactive_login(sigstore_provider):
    sigstore_provider.token = None
    with pytest.raises(AttestationError) as exc:
        AttestationService(allow_public_signing=True).sign(
            ARTIFACT, artifact_name=ARTIFACT_NAME
        )
    assert exc.value.code == "signing-credentials-unavailable"
    assert sigstore_provider.credential_calls == 1
    assert sigstore_provider.public_trust_calls == 0
    assert sigstore_provider.signing_calls == 0


def test_sign_binds_exact_existing_output_without_solve_claims(sigstore_provider):
    bundle = AttestationService(allow_public_signing=True).sign(
        ARTIFACT, artifact_name=ARTIFACT_NAME
    )
    statement = sigstore_provider.statement
    assert bundle == "signed-bundle"
    assert sigstore_provider.credential_calls == 1
    assert statement["predicateType"] == LINK_PREDICATE_TYPE
    assert statement["subject"] == [
        {
            "name": ARTIFACT_NAME,
            "digest": {"sha256": hashlib.sha256(ARTIFACT).hexdigest()},
        }
    ]
    assert statement["predicate"] == {
        "name": SIGNING_STEP,
        "command": [],
        "materials": statement["subject"],
        "byproducts": {},
        "environment": {},
    }


def test_sign_uses_configured_trust_without_public_default(sigstore_provider, tmp_path):
    config = tmp_path / "trust.json"
    AttestationService(trust_config=config).sign(ARTIFACT, artifact_name=ARTIFACT_NAME)
    assert sigstore_provider.private_trust_reads == [(config, 1000)]
    assert sigstore_provider.public_trust_calls == 0


def test_sign_rejects_a_locally_verified_different_payload(sigstore_provider):
    sigstore_provider.payload_override = b"different-payload"
    with pytest.raises(AttestationError) as exc:
        AttestationService(allow_public_signing=True).sign(
            ARTIFACT, artifact_name=ARTIFACT_NAME
        )
    assert exc.value.code == "signing-failed"


def test_sign_sanitizes_provider_errors(sigstore_provider):
    sigstore_provider.verification_error = RuntimeError("secret-provider-detail")
    with pytest.raises(AttestationError) as exc:
        AttestationService(allow_public_signing=True).sign(
            ARTIFACT, artifact_name=ARTIFACT_NAME
        )
    assert exc.value.code == "signing-failed"
    assert "secret-provider-detail" not in str(exc.value)


def test_capability_probe_does_not_discover_credentials(sigstore_provider):
    assert AttestationService.available() is True
    assert sigstore_provider.credential_calls == 0
    assert sigstore_provider.public_trust_calls == 0


def test_missing_provider_reports_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "conda_sigstore.statements", None)
    assert AttestationService.available() is False
    with pytest.raises(AttestationError) as exc:
        AttestationService(allow_public_signing=True).sign(
            ARTIFACT, artifact_name=ARTIFACT_NAME
        )
    assert exc.value.code == "provider-unavailable"


def test_installed_provider_rejects_invalid_bundle_without_network():
    with pytest.raises(AttestationError) as exc:
        AttestationService(offline=True).verify(
            ARTIFACT,
            "{}",
            artifact_name=ARTIFACT_NAME,
            expected_identity=IDENTITY,
            expected_issuer=ISSUER,
        )
    assert exc.value.code == "invalid-bundle"


@pytest.mark.parametrize(
    "change, code",
    [
        pytest.param(None, None, id="valid-offline-bundle"),
        pytest.param("artifact", "artifact-mismatch", id="changed-bytes"),
        pytest.param("identity", "untrusted-identity", id="wrong-identity"),
        pytest.param("issuer", "untrusted-identity", id="wrong-issuer"),
        pytest.param("signature", "invalid-bundle", id="changed-signature"),
    ],
)
def test_real_bundle_verification_binds_bytes_and_expected_signer(
    monkeypatch, change, code
):
    def reject_network(*args, **kwargs):
        pytest.fail("Offline fixture verification attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setattr(socket.socket, "connect_ex", reject_network)
    fixtures = Path(__file__).parent / "fixtures" / "sigstore"
    body = (fixtures / "artifact.txt").read_bytes()
    bundle = (fixtures / "bundle.sigstore.json").read_text(encoding="utf-8")
    identity = (
        "https://github.com/sigstore-conformance/"
        "extremely-dangerous-public-oidc-beacon/.github/workflows/"
        "extremely-dangerous-oidc-beacon.yml@refs/heads/main"
    )
    issuer = "https://token.actions.githubusercontent.com"
    if change == "artifact":
        body += b"\n"
    elif change == "identity":
        identity = "https://example.org/different-workflow"
    elif change == "issuer":
        issuer = "https://example.org/different-issuer"
    elif change == "signature":
        parsed = json.loads(bundle)
        signature = parsed["dsseEnvelope"]["signatures"][0]
        original = signature["sig"]
        signature["sig"] = ("A" if original[0] != "A" else "B") + original[1:]
        bundle = json.dumps(parsed)

    service = AttestationService(offline=True, trust_config=fixtures / "trust.json")
    kwargs = {
        "artifact_name": "a.txt",
        "expected_identity": identity,
        "expected_issuer": issuer,
    }
    if code:
        with pytest.raises(AttestationError) as exc:
            service.verify(body, bundle, **kwargs)
        assert exc.value.code == code
    else:
        result = service.verify(body, bundle, **kwargs)
        assert result["signature_verified"] is True
        assert result["artifact_verified"] is True
        assert result["signer_verified"] is True
        assert result["artifact_sha256"] == (
            "a0cfc71271d6e278e57cd332ff957c3f7043fdda354c4cbb190a30d56efa01bf"
        )
        assert result["predicate_type"] == "https://slsa.dev/provenance/v1"
        assert result["claims_checked"] is False


def test_worker_returns_safe_errors_from_an_actual_child():
    with pytest.raises(AttestationError) as exc:
        AttestationService().run_until(
            "sign",
            deadline=time.monotonic() + 20,
            body=ARTIFACT,
            artifact_name=ARTIFACT_NAME,
        )
    assert exc.value.code == "signing-disabled"


def test_worker_kills_a_process_that_ignores_termination(monkeypatch):
    state = SimpleNamespace(alive=False, terminated=False, killed=False, closed=[])
    receiver = SimpleNamespace(
        poll=lambda timeout: False,
        close=lambda: state.closed.append("receiver"),
    )
    sender = SimpleNamespace(close=lambda: state.closed.append("sender"))
    process = SimpleNamespace(
        pid=1,
        start=lambda: setattr(state, "alive", True),
        is_alive=lambda: state.alive,
        terminate=lambda: setattr(state, "terminated", True),
        kill=lambda: (
            setattr(state, "killed", True),
            setattr(state, "alive", False),
        ),
        join=lambda timeout: None,
        close=lambda: state.closed.append("process"),
    )
    monkeypatch.setattr(
        attestation_module.multiprocessing,
        "get_context",
        lambda method: SimpleNamespace(
            Pipe=lambda **kwargs: (receiver, sender),
            Process=lambda **kwargs: process,
        ),
    )
    with pytest.raises(TimeoutError):
        AttestationService().run_until("verify", deadline=time.monotonic() + 20)
    assert state.terminated is True
    assert state.killed is True
    assert {"receiver", "sender", "process"}.issubset(state.closed)


def test_worker_rejects_an_already_expired_deadline():
    with pytest.raises(TimeoutError):
        AttestationService().run_until("sign", deadline=time.monotonic() - 1)
