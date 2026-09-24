# Configuration

Set application environment variables before starting the process. They are read when conda-presto imports its configuration. Conda settings come from the active conda context. CLI flags override only the corresponding options.

Integer settings use decimal strings. Boolean settings accept `1`, `true`, `yes`, `0`, `false` or `no`, ignoring case. Empty values use defaults. Comma-separated settings trim whitespace and omit empty items. Invalid settings fail at startup.

## Channels and platforms

Ordinary HTTP solves use channels from the request, then the input file, then `CONDA_PRESTO_CHANNELS`. Ordinary CLI solves use conda's effective channels unless they are empty or only `defaults`, then use file channels or `CONDA_PRESTO_CHANNELS`. The server checks channels against `CONDA_PRESTO_ALLOWED_CHANNELS` using resolved URLs.

Ordinary solves use explicit platforms or default to conda's startup subdir. `CONDA_PRESTO_PLATFORMS` only selects startup warmup targets. Presto's OS-version settings provide virtual-package defaults for each target, including the native subdir. Conda virtual-package plugins and `CONDA_OVERRIDE_*` values can also affect these solves.

Workspace solves use channels and requirements composed from the selected manifest environments and targets. They reject extra specs and channel overrides. Without selectors, they solve every declared environment and target. A manifest with no declared platforms requires an explicit conda subdir. A subdir selector must identify one declared target unambiguously when targets are declared.

Workspace targets start with Presto's OS-version defaults, then apply manifest system requirements and suppress host virtual-package detections and ambient `CONDA_OVERRIDE_*` values for overridable plugins. Effective target virtual-package records participate in result-cache identity. See {doc}`/how-to/extract-workspace-lock` for saved target selection and {doc}`/tutorials/workspaces` for updates.

## Execution and storage

`CONDA_PRESTO_CONCURRENCY` limits simultaneous foreground requests. `CONDA_PRESTO_WORKERS` controls processes within an ordinary multiplatform solve. Workspace solves process selected environment/target pairs sequentially. Persistent mode reuses one worker, including indexes from ordinary solves. The server image enables that mode with one foreground slot.

When no backend is explicit, a Redis URL selects Redis, otherwise a configured directory selects file storage, otherwise memory is used. See {doc}`cache` for limits and freshness.

## SBOMs, signing and verification

Standard installations and the server image include conda-sboms for SBOM generation and conda-sigstore for signing and verification. Available operations and signing configuration are reported through `/capabilities`.

Presto uses conda-sigstore's statement and verification APIs, which work with released conda. Conda-sigstore's separate package-install verification requires the unreleased conda hook proposed in [conda #16518](https://github.com/conda/conda/pull/16518). That hook is not needed by Presto's `/sign` or `/verify` endpoints.

Signing is disabled by default. It requires explicit enablement, noninteractive identity credentials and a configured trust choice. A trust-file path alone does not enable signing. Verification receives the recipient's expected identity and issuer with the request. Follow {doc}`/how-to/sign-and-verify` for an operator recipe, {doc}`environment-variables` for exact settings and {doc}`http-api` for fields.

## Source workspace

`prod` runs the service, `cli` runs one-shot commands, `test` includes provider checks, `dev` provides lint and server tools, and `docs` builds Sphinx. All include conda-sboms and conda-sigstore. Changing Pixi metadata requires regenerating `pixi.lock`.
