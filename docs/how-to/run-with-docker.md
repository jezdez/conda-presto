# Run conda-presto with Docker

{download}`Runnable example <../../demos/docker.sh>`

Build current source with the canonical server recipe:

```bash
docker build --build-arg CONDA_PRESTO_VERSION=0.9.0.dev0 \
  --tag conda-presto:dev .
export CONDA_PRESTO_SERVER_IMAGE=conda-presto:dev
```

For a published deployment, set `CONDA_PRESTO_SERVER_IMAGE` to an exact released tag or manifest digest. See {doc}`../reference/docker-images` for tags and build configuration.

## Run the HTTP server

Start the server on the loopback interface:

```bash
docker run --detach \
  --name conda-presto \
  --publish 127.0.0.1:8000:8000 \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  "$CONDA_PRESTO_SERVER_IMAGE"
```

:::{important}
The explicit `127.0.0.1` binding keeps the published port local to the Docker
host. Do not replace it with an all-interface binding unless the deployment has
an appropriate network access controls.
The image already runs as UID 10001. Dropping capabilities and setting
`no-new-privileges` also constrains inherited container privileges.
:::

The image has a built-in health check. Wait for it before sending solves:

```bash
until [ "$(docker inspect --format '{{.State.Health.Status}}' conda-presto)" = healthy ]
do
  sleep 2
done

curl --fail --silent --show-error http://127.0.0.1:8000/health
```

The generated OpenAPI document is available at `http://127.0.0.1:8000/openapi.json`.

Inspect the server logs when startup takes longer than expected:

```bash
docker logs --follow conda-presto
```

## Configure the server

Pass application settings as environment variables:

```bash
docker run --detach \
  --name conda-presto \
  --publish 127.0.0.1:8000:8000 \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --env CONDA_PRESTO_CHANNELS=conda-forge,bioconda \
  --env CONDA_PRESTO_ALLOWED_CHANNELS=conda-forge,bioconda \
  --env CONDA_PRESTO_PLATFORMS=linux-64,osx-arm64 \
  --env CONDA_PRESTO_WORKERS=2 \
  "$CONDA_PRESTO_SERVER_IMAGE"
```

The server preloads the configured channel and platform combinations before
`/health` reports ready. See {doc}`../reference/environment-variables` for
limits and defaults.

:::{note}
The health check follows `CONDA_PRESTO_PORT`. Keep the published container port aligned with that setting.
:::

## Keep a file-backed result cache

The image runs as UID and GID 10001. Initialize a Docker-managed volume with
matching ownership:

```bash
docker volume create conda-presto-results

docker run --rm \
  --user root \
  --entrypoint /usr/bin/chown \
  --mount source=conda-presto-results,target=/var/cache/conda-presto \
  "$CONDA_PRESTO_SERVER_IMAGE" \
  10001:10001 /var/cache/conda-presto
```

Apply a hard size quota to the backing volume or filesystem. The file backend
performs best-effort expiry cleanup, but expiry does not cap aggregate disk use.

Start the server with that volume:

```bash
docker run --detach \
  --name conda-presto \
  --publish 127.0.0.1:8000:8000 \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --mount source=conda-presto-results,target=/var/cache/conda-presto \
  --env CONDA_PRESTO_RESULT_CACHE_BACKEND=file \
  --env CONDA_PRESTO_RESULT_CACHE_DIR=/var/cache/conda-presto/results \
  "$CONDA_PRESTO_SERVER_IMAGE"
```

Use {doc}`configure-result-cache` for cache verification and Redis setup.

## Replace or stop the container

Remove the running container without deleting named cache volumes:

```bash
docker rm --force conda-presto
```

Use {doc}`monitor-service` for readiness and logs. The one-shot CLI remains available from the Python package as described in {doc}`resolve-from-cli`.
