# Configuration

Set application environment variables before starting the process. They are read when conda-presto imports its configuration. Conda settings come from the active conda context. CLI flags override only the corresponding options.

Integer settings use decimal strings. Boolean settings accept `1`, `true`, `yes`, `0`, `false` or `no`, ignoring case. Empty values use defaults. Comma-separated settings trim whitespace and omit empty items. Invalid settings fail at startup.

## Channels and platforms

HTTP requests use channels from the request, then the input file, then `CONDA_PRESTO_CHANNELS`. The CLI also considers conda's effective channels. The server checks channels against `CONDA_PRESTO_ALLOWED_CHANNELS` using resolved URLs.

Request platforms are explicit, or default to the native platform. `CONDA_PRESTO_PLATFORMS` only selects startup warmup targets. Target virtual-package overrides and other effective conda virtual records participate in result-cache identity.

## Execution and storage

`CONDA_PRESTO_CONCURRENCY` limits simultaneous foreground requests. `CONDA_PRESTO_WORKERS` controls processes within a multiplatform solve. Persistent mode reuses one worker and its indexes. The server image enables that mode with one foreground slot.

When no backend is explicit, a Redis URL selects Redis, otherwise a configured directory selects file storage, otherwise memory is used. See {doc}`cache` for limits and freshness.

## SBOMs, signing and verification

Standard installations and the server image include conda-sboms for SBOM generation and conda-sigstore for signing and verification. Available operations and signing configuration are reported through `/capabilities`.

Presto uses conda-sigstore's statement and verification APIs, which work with released conda. Conda-sigstore's separate package-install verification requires the unreleased conda hook proposed in [conda #16518](https://github.com/conda/conda/pull/16518). That hook is not needed by Presto's `/sign` or `/verify` endpoints.

Signing is disabled by default. It requires explicit enablement, noninteractive identity credentials and a configured trust choice. A trust-file path alone does not enable signing. Verification receives the recipient's expected identity and issuer with the request. See {doc}`environment-variables` for exact settings and {doc}`http-api` for fields.

## Source workspace

`prod` runs the service, `cli` runs one-shot commands, `test` includes provider checks, `dev` provides lint and server tools, and `docs` builds Sphinx. All include conda-sboms and conda-sigstore. Changing Pixi metadata requires regenerating `pixi.lock`.
