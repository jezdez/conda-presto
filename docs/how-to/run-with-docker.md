# Run conda-presto with Docker

conda-presto provides an HTTP server image and a one-shot CLI image. The
examples pin the 0.7.0 release.

## Choose image versions

Set the image variables once, then use the remaining commands unchanged.

`````{tab-set}

````{tab-item} 0.7.0 release
Select the exact server and CLI release tags:

```bash
export CONDA_PRESTO_SERVER_IMAGE=ghcr.io/jezdez/conda-presto:0.7.0
export CONDA_PRESTO_CLI_IMAGE=ghcr.io/jezdez/conda-presto:0.7.0-cli
```
````

````{tab-item} Current main
To test unpublished changes, build both targets from a source checkout with
explicit local names:

```bash
docker build -f docker/Dockerfile \
  --target server \
  --build-arg PIXI_ENV=prod \
  --build-arg CONDA_PRESTO_VERSION=0.7.1.dev0 \
  --tag conda-presto-server:main .

docker build -f docker/Dockerfile \
  --target cli \
  --build-arg PIXI_ENV=cli \
  --build-arg CONDA_PRESTO_VERSION=0.7.1.dev0 \
  --tag conda-presto-cli:main .

export CONDA_PRESTO_SERVER_IMAGE=conda-presto-server:main
export CONDA_PRESTO_CLI_IMAGE=conda-presto-cli:main
```
````

`````

## Run the HTTP server

Start the server on the loopback interface:

```bash
docker run --detach \
  --name conda-presto \
  --publish 127.0.0.1:8000:8000 \
  "$CONDA_PRESTO_SERVER_IMAGE"
```

:::{important}
The explicit `127.0.0.1` binding keeps the published port local to the Docker
host. Do not replace it with an all-interface binding unless the deployment has
an appropriate network and authentication boundary.
:::

The image has a built-in health check. Wait for it before sending solves:

```bash
until [ "$(docker inspect --format '{{.State.Health.Status}}' conda-presto)" = healthy ]
do
  sleep 2
done

curl --fail --silent --show-error http://127.0.0.1:8000/health
```

Inspect the server logs when startup takes longer than expected:

```bash
docker logs --follow conda-presto
```

## Run the one-shot CLI

The `cli` image passes its arguments to `conda presto`:

```bash
docker run --rm \
  "$CONDA_PRESTO_CLI_IMAGE" \
  --channel conda-forge \
  --platform linux-64 \
  python=3.13 numpy
```

## Read a host file from the CLI image

Mount the current directory before referring to a host file:

```bash
docker run --rm \
  --mount type=bind,source="$PWD",target=/work,readonly \
  --workdir /work \
  "$CONDA_PRESTO_CLI_IMAGE" \
  --file environment.yml \
  --platform linux-64 \
  --format pixi-lock-v6 \
  > pixi.lock
```

Shell redirection writes `pixi.lock` on the host. A container cannot see
`environment.yml` unless the containing directory is mounted.

## Configure the server

Pass application settings as environment variables:

```bash
docker run --detach \
  --name conda-presto \
  --publish 127.0.0.1:8000:8000 \
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
The built-in health check always calls container port 8000. If
`CONDA_PRESTO_PORT` changes the application port, replace the container health
check too.
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

Start the server with that volume:

```bash
docker run --detach \
  --name conda-presto \
  --publish 127.0.0.1:8000:8000 \
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

## Docker boundaries

The server image uses a persistent foreground worker, but it does not start
conda-broker. It does not expose the internal `/solver/v1` route and cannot be
used as a `conda --solver=presto` target. Scheduled solver-result refresh also
runs only in the broker-managed service.

For a local solver backend, use {doc}`run-broker-service` and
{doc}`use-presto-solver`. For readiness and logs, see
{doc}`monitor-service`. See {doc}`../reference/docker-images` for image tags,
platforms, defaults, and health-check settings.
