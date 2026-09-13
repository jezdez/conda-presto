# Offline Sigstore verification fixture

These public upstream fixtures exercise real certificate, transparency-log, DSSE signature, artifact digest and signer checks without network access or signing credentials. The expected signer is accepted only by the test's explicit identity, issuer and staging trust configuration.

- `artifact.txt` is the unchanged [sigstore-conformance test input at bf6b322](https://github.com/sigstore/sigstore-conformance/blob/bf6b322ef65839216ec8853287032750e1f4b92d/test/assets/a.txt). Its SHA256 is `a0cfc71271d6e278e57cd332ff957c3f7043fdda354c4cbb190a30d56efa01bf`, matching the bundle's `a.txt` subject.
- `bundle.sigstore.json` is the unchanged [sigstore-python v4.5.0 Rekor v2 DSSE fixture](https://github.com/sigstore/sigstore-python/blob/v4.5.0/test/assets/a.dsse.staging-rekor-v2.txt.sigstore.json). Its SHA256 is `56e79ba9f94a34aba285769574c456601f38beeaa48a3acb67f8d5db706f9420`.
- `trust.json` wraps the unchanged `trusted_root.json` and `signing_config.v0.2.json` objects from [sigstore-python v4.5.0's bundled staging configuration](https://github.com/sigstore/sigstore-python/tree/v4.5.0/sigstore/_store/https%253A%252F%252Ftuf-repo-cdn.sigstage.dev) in a standard Sigstore client trust configuration. Its SHA256 is `f879118d02ab6e2a411581fedc6b96adf8860895a10955a0c8bf2994b9744505`.

The bundle authenticates an upstream SLSA statement. Presto checks its artifact and signer here but leaves its provenance claims unchecked. The similarly named text file beside the bundle in sigstore-python does not match the signed digest, so this test uses the original conformance input.

The fixtures originate from the Sigstore projects, distributed under the Apache License 2.0. The upstream license is included in `LICENSE`.
