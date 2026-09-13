# Docker image reference

One server image is published to `ghcr.io/jezdez/conda-presto` for `linux/amd64` and `linux/arm64`. The root `Dockerfile` is the canonical recipe. The image runs as UID and GID 10001 from `/app` and starts `conda presto --serve --host 0.0.0.0` on port 8000.

## Tags and publication

| Tag | Selection |
|---|---|
| `latest` | Latest released server image |
| `<version>` | Immutable exact release |
| `<major>.<minor>` | Mutable minor-line alias |
| `<short-git-sha>` | Immutable source revision |

Major-zero releases do not publish a `0` alias. Publication refuses to overwrite an exact release or source revision tag. The release workflow dispatches the image build from the released tag and uses the protected `ghcr` environment. Pin a manifest digest when reproducibility must be independent of tag policy.

## Image verification

BuildKit publishes maximum-mode provenance and an SPDX SBOM for each image manifest. A separate job creates a GitHub artifact attestation after the registry returns the pushed digest. For an existing release:

```bash
gh attestation verify \
  oci://ghcr.io/jezdez/conda-presto:0.8.0 \
  --repo jezdez/conda-presto \
  --signer-workflow jezdez/conda-presto/.github/workflows/docker.yml
```

CI builds and scans the server on amd64 and arm64 before publication. Trivy blocks fixed high and critical findings, subject to narrow expiring exceptions. Full reports are uploaded as `trivy-server-amd64` and `trivy-server-arm64`. Trivy does not provide a complete vulnerability inventory for conda packages.

Build and runtime base images are pinned by digest. The recipe applies the Debian PCRE2 security update, removes setuid and setgid permissions, and keeps application source and the environment outside its package cache read-only for the runtime user.

## Worker and health check

The image sets `CONDA_PRESTO_PERSISTENT_WORKER=1` and `CONDA_PRESTO_CONCURRENCY=1`. It applies `CONDA_NO_LOCK=false` after Pixi activation so workers sharing the package cache retain conda filesystem locking. A failed worker is replaced by the server.

The built-in health check requests `/health` on `CONDA_PRESTO_PORT`, defaulting to 8000. It runs every 30 seconds with a 5-second timeout, 120-second startup period and three retries. It reports failure while the persistent worker is unavailable. Deployments that require another port, including a Space on port 7860, can set `CONDA_PRESTO_PORT=7860` while using the same recipe.

## Build configuration

```bash
docker build --build-arg CONDA_PRESTO_VERSION=0.9.0.dev0 \
  --tag conda-presto:dev .
```

`PIXI_ENVIRONMENT` selects the locked environment, defaulting to `prod`. Optional SBOM and signing providers are absent from the default image. Operators can build the same recipe with `--build-arg PIXI_ENVIRONMENT=artifacts` to include both providers. This does not create another published image flavor.

The default image includes the Redis client. Cache settings are described in {doc}`cache`. See {doc}`/how-to/run-with-docker` for operation.
