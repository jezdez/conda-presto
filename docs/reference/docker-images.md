# Docker image reference

Release images are published to `ghcr.io/jezdez/conda-presto` for
`linux/amd64` and `linux/arm64`. Both flavors use `debian:bookworm-slim`, run
from `/app`, and use UID and GID 10001. Build and runtime base images are pinned
by tag and multi-platform digest.

## Image flavors

| Flavor | Docker target | Pixi environment | Entrypoint | Default command |
|---|---|---|---|---|
| Server | `server` | `prod` | `conda presto` | `--serve --host 0.0.0.0` |
| CLI | `cli` | `cli` | `conda presto` | None |

Arguments after the CLI image name are passed directly to `conda presto`. The
server exposes container port 8000.

## Published tags

For a release such as `v0.7.0`, the publishing workflow produces:

| Server tag | CLI tag | Selection |
|---|---|---|
| `latest` | `cli` | Mutable flavor alias updated by each release |
| `0.7.0` | `0.7.0-cli` | Immutable exact release tag |
| `0.7` | `0.7-cli` | Mutable minor-line alias updated within the release line |
| `<short-git-sha>` | `<short-git-sha>-cli` | Immutable tag for the verified source revision |

Major-zero releases do not publish `0` or `0-cli` because those aliases could
cross incompatible minor lines. Before publishing, the workflow refuses to
overwrite an existing exact release tag or short source-SHA tag. The `latest`,
`cli`, minor, and nonzero-major aliases move. A short SHA can theoretically
collide, in which case publication fails rather than replacing the existing
tag. Publication requires the tagged source commit's CI and CodeQL checks to
have passed, then waits for approval through the protected `ghcr` environment.
That environment accepts only `v*` tags. Pin the image manifest digest when
deployment reproducibility must not depend on tag policy or registry
administration.

## Provenance and image contents

The release workflow publishes maximum-mode BuildKit provenance and an SPDX
SBOM for each image manifest. A separate job signs a GitHub artifact
attestation only after the registry returns the pushed digest. Verify an exact
release image with GitHub CLI after authenticating to GHCR:

```bash
gh attestation verify \
  oci://ghcr.io/jezdez/conda-presto:0.7.0 \
  --repo jezdez/conda-presto \
  --signer-workflow jezdez/conda-presto/.github/workflows/docker.yml
```

The production filesystem keeps application source, the entrypoint, and the
installed Pixi environment outside its package cache owned by root. UID 10001
can write `/app/.pixi/envs/<environment>/pkgs` and its home directory at
`/home/app`. The build removes setuid and setgid bits from inherited utilities.

## Vulnerability scans

Pull requests build and scan the server and CLI images on `linux/amd64`, then
build and scan both flavors on `linux/arm64` under QEMU. Release publication
depends on those jobs. Trivy fails on fixed high or critical findings that it
recognizes, subject to the narrow, expiring exception in `.trivyignore.yaml`.

Each image job also uploads a nonblocking SARIF report containing all severities,
including findings without an available fix and findings covered by the blocking
scan's exception. Download the `trivy-<flavor>-<architecture>` artifact from the
workflow run to inspect it. Trivy does not treat conda package records as a
supported package ecosystem. It can recognize operating-system packages and
language package metadata present inside a Pixi environment, but a passing scan
is not a complete vulnerability inventory for conda packages.

## Server defaults

The server target sets:

| Variable | Value |
|---|---|
| `CONDA_NO_LOCK` | `false` |
| `CONDA_PRESTO_CONCURRENCY` | `1` |
| `CONDA_PRESTO_PERSISTENT_WORKER` | `1` |

The server entrypoint applies `CONDA_NO_LOCK=false` after the shared Pixi shell
hook. This keeps conda filesystem locking enabled while the server and its
persistent worker share the package cache.

The persistent worker loads the configured channel and platform indexes before
the application reports ready. It retains that state between requests. The
server process replaces a worker after failure or timeout.

The server image includes the Redis client from the `prod` environment. File
and Redis result-cache settings use the same environment variables as other
deployments.

The image contains conda-broker because it is a required package dependency,
but it does not start the broker. Without the broker service identity, the
private `/solver/v1` route is unavailable and scheduled solver-cache refresh is
disabled.

## Health check

The server image defines an HTTP health check against
`http://127.0.0.1:8000/health` using the production environment's Python
interpreter.

| Setting | Value |
|---|---:|
| Interval | 30 seconds |
| Timeout | 5 seconds |
| Start period | 120 seconds |
| Retries | 3 |
| Request timeout | 3 seconds |

The endpoint returns HTTP 503 while a persistent worker is unavailable. The
container health check becomes successful again after the replacement worker
has loaded its indexes.

The default server command fixes the bind host at `0.0.0.0`, so
`CONDA_PRESTO_HOST` does not override it. The command leaves the port to the
application default, but the Docker health check always uses port 8000. If
`CONDA_PRESTO_PORT` changes the listening port, the built-in health check must
also be replaced by the deployment.

## Build targets

Both targets use the same multi-stage Dockerfile. `PIXI_ENV` selects the
runtime environment and `CONDA_PRESTO_VERSION` supplies the package version to
the source build.

```bash
docker build -f docker/Dockerfile --target server \
  --build-arg PIXI_ENV=prod \
  --build-arg CONDA_PRESTO_VERSION=0.7.0 \
  -t conda-presto-server .

docker build -f docker/Dockerfile --target cli \
  --build-arg PIXI_ENV=cli \
  --build-arg CONDA_PRESTO_VERSION=0.7.0 \
  -t conda-presto-cli .
```

## See also

- {doc}`/how-to/run-with-docker`
- {doc}`/reference/environment-variables`
- {doc}`/reference/cache`
- {doc}`/reference/observability`
- {doc}`/explanation/security`
