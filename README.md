# conda-presto

conda-presto resolves conda package specifications without creating or changing an environment. It accepts inline specs or environment files, selects fully pinned packages for one or more platforms, and writes native JSON or a conda exporter format.

The same solve engine is available through the `conda presto` CLI, a Litestar HTTP API, a GitHub Action, and an internal broker-backed `conda --solver=presto` plugin. The internal solver delegates only final-state selection. Conda still owns package downloads and prefix transactions locally.

## Capabilities

- Resolve inline specs or supported environment inputs selected through conda environment specifier plugins
- Solve several target platforms with deterministic target virtual-package defaults
- Write native package metadata or any installed conda exporter format
- Convert covered lockfiles through the CLI without solving
- Inspect lockfile metadata over HTTP without loading package records
- Parse and review inputs through `/parse`, `/preflight`, `/repair`, `/diff`, and `/explain`
- Prepare, resolve, examine, and export environments in the first-party browser workbench
- Retain HTTP results under content-addressed `/r/<hash>` locations with memory, file, or Redis storage
- Run a persistent public HTTP worker in the server container
- Run an opt-in broker-managed loopback service for repeated local work
- Delegate internal conda final-state solves through `conda --solver=presto`
- Record repeated solver requests and refresh missing or stale private final states during broker idle time
- Resolve environments in GitHub Actions through local CLI or remote HTTP modes
- Select packages with `conda-rattler-solver`

## Quick start

```bash
conda create --name conda-presto \
  --override-channels \
  --channel conda-forge \
  python=3.13 \
  'conda>=26.5,<27' \
  'conda-rattler-solver>=0.1.1,<0.2' \
  'conda-lockfiles>=0.2.1' \
  pip
conda activate conda-presto
python -m pip install conda-presto
conda presto -c conda-forge -p linux-64 python=3.13 numpy
```

conda-presto is installed from PyPI into a conda environment that supplies conda and its solver plugins. It is not currently published as a conda package. See the [quick start](https://jezdez.github.io/conda-presto/quickstart/) for released, current-main, and source-checkout installation paths, file inputs, lockfile output, and the local HTTP server.

## Run the HTTP server

```bash
docker run --detach \
  --name conda-presto \
  --publish 127.0.0.1:8000:8000 \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  ghcr.io/jezdez/conda-presto:0.8.0
for _ in {1..180}
do
  if curl --fail --silent http://127.0.0.1:8000/health >/dev/null
  then
    break
  fi
  sleep 1
done
curl --fail --silent --show-error http://127.0.0.1:8000/health
```

The server image listens on port 8000 inside the container and runs one persistent foreground worker. It does not start conda-broker, expose the internal solver route, or run scheduled solver-cache refresh.

The 0.8.0 server image serves the browser workbench at `http://127.0.0.1:8000/` and the generated OpenAPI document at `http://127.0.0.1:8000/openapi.json`.

See [Run conda-presto with Docker](https://jezdez.github.io/conda-presto/how-to/run-with-docker/) and the [Docker image reference](https://jezdez.github.io/conda-presto/reference/docker-images/).

## Run the broker-managed service

conda-presto registers the manual `conda-presto.server` service with conda-broker. The service binds to a broker-assigned loopback port and keeps its foreground solver worker loaded between requests.

```bash
conda broker start conda-presto.server
conda broker wait conda-presto.server --timeout 180
conda broker endpoint conda-presto.server
```

See [Run the broker-managed service](https://jezdez.github.io/conda-presto/how-to/run-broker-service/) for lifecycle, configuration capture, logs, and trust boundaries.

## Use the internal Presto solver

After the broker service is ready, select the internal backend for one conda operation:

```bash
conda create --dry-run --solver=presto -n demo -c conda-forge python=3.13
```

The backend is local-only and tied to the broker service. It is not a remote solver protocol and is not available through the Docker server image. See [Use the Presto solver](https://jezdez.github.io/conda-presto/how-to/use-presto-solver/) and the [solver reference](https://jezdez.github.io/conda-presto/reference/solver-backend/).

## Documentation

- [Tutorials](https://jezdez.github.io/conda-presto/tutorials/) for guided CLI, HTTP, review, and local-service workflows
- [How-to guides](https://jezdez.github.io/conda-presto/how-to/) for Docker, broker, cache, monitoring, troubleshooting, and CI tasks
- [Reference](https://jezdez.github.io/conda-presto/reference/) for exact interfaces, service contracts, cache behavior, and configuration
- [Explanation](https://jezdez.github.io/conda-presto/explanation/) for architecture, deployment models, review semantics, caching, performance, and security
- [Roadmap](https://jezdez.github.io/conda-presto/proposals/) for shipped streams and linked future work
- [Changelog](https://jezdez.github.io/conda-presto/changelog/) for release history

## Development

```bash
git clone https://github.com/jezdez/conda-presto.git
cd conda-presto
pixi install
pixi run lint
pixi run format
pixi run -e test test
pixi run -e test bench
pixi run serve
pixi run -e docs docs
```

## License

BSD-3-Clause
