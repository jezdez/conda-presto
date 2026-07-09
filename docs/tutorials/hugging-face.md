# Deploy on Hugging Face Spaces

[Hugging Face Docker Spaces](https://huggingface.co/docs/hub/en/spaces-sdks-docker) can run conda-presto as a public HTTP API. This is the easiest free deployment path when you want the examples in the HTTP API tutorial to work with `curl`.

The repository root includes the files Hugging Face needs:

- `README.md` has the [Space metadata block](https://huggingface.co/docs/hub/en/spaces-config-reference) with `sdk: docker` and `app_port: 7860`.
- `Dockerfile` uses the Pixi base image, installs the locked `prod` environment, and starts `conda presto --serve`.

## Create the Space

Create a new Hugging Face Space and choose the Docker SDK. The Space name becomes part of the public API URL:

```text
https://<namespace>-<space>.hf.space
```

Push this repository to the Space remote:

```bash
git remote add hf https://huggingface.co/spaces/<namespace>/<space>
git push hf main
```

Hugging Face builds the root `Dockerfile` and exposes port `7860`.

## Runtime defaults

The Space Dockerfile sets conservative defaults for the free CPU runtime:

- `CONDA_PRESTO_CONCURRENCY=1`
- `CONDA_PRESTO_WORKERS=1`
- `CONDA_PRESTO_PLATFORMS=linux-64`

Those defaults keep startup and warmup modest. Increase them only after moving to a larger Space.

The container runs as the Pixi image's UID `1000` user, and the Dockerfile creates writable conda package-cache directories under `/app` and `/home/ubuntu`.

## Test the API

Check that the service is alive:

```bash
curl -sS https://<namespace>-<space>.hf.space/health
```

Resolve inline specs:

```bash
curl -sS https://<namespace>-<space>.hf.space/resolve \
  -H 'Content-Type: application/json' \
  --data '{
    "channels": ["conda-forge"],
    "specs": ["python=3.13", "numpy"],
    "platforms": ["linux-64"]
  }'
```

Resolve an environment file and return a Pixi lockfile:

```bash
curl -sS --data-binary @examples/environment.yml \
  -H 'Content-Type: application/yaml' \
  'https://<namespace>-<space>.hf.space/resolve?filename=environment.yml&format=pixi-lock-v6&platform=linux-64' \
  > pixi.lock
```

## Operational notes

Docker Spaces expose one public app port. Extra services can run inside the container, but only the configured app port is reachable from the internet.

The free runtime can restart or sleep, and data written to the local filesystem is not durable across restarts. conda-presto keeps an in-process result cache and writable package caches for warm requests, but treat them as disposable.
