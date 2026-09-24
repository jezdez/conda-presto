# Offline verification fixture

This demo calls Presto's `AttestationService.verify()` directly. It uses the public artifact, signed bundle and staging trust configuration documented in `tests/fixtures/sigstore/README.md`. The fixture files and their Apache 2.0 license remain in that directory.

`policy.json` fixes the expected subject name, signer and issuer before verification. These values match the existing real-provider test in `tests/test_attestation.py`. The demo does not derive the expected signer from the bundle being verified. This staging signer is accepted only for the public test fixture and is not a production trust policy.

The original bytes pass real certificate, transparency-log, DSSE signature, artifact digest and signer checks. Appending a newline fails with `artifact-mismatch`. Choosing another expected signer fails with `untrusted-identity`. The fixture's SLSA provenance claims remain unchecked, shown by `claims_checked: false`.

No artifact is signed, no credentials are acquired and verification runs with `offline=True`.
