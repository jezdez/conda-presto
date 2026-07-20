# conda-presto

A fast conda solve engine with a dry-run CLI and HTTP API. Given package specs
or an environment file (`environment.yml`,
`pixi.toml`, `pyproject.toml`, `requirements.txt`, conda-lock,
pixi-lock, …), it resolves fully pinned packages for one or more
platforms — without downloading or installing anything — and emits
the result as native JSON or any conda exporter format
(`pixi.lock`, `conda-lock.yml`, environment YAML, explicit file, …).

The optional internal `conda --solver=presto` plugin delegates only final-state
solving to the local broker service. For commands without `--dry-run`, conda
still performs its normal package download and prefix transaction locally.

## Highlights

- Resolve inline specs or any environment file format
- Full package metadata: sha256, md5, urls, sizes, depends
- Cross-platform solving with automatic virtual package injection
- Multi-platform parallel solves via `ProcessPoolExecutor`
- Output as JSON or any conda exporter format (`--format` / `?format=`)
- Lockfile-to-lockfile transcode path that skips solving when possible
- Review proposed environments with `/preflight`, `/diff`, and `/explain`
- Content-addressed HTTP result cache with `/r/<sha256>` lookups and optional file or Redis backing
- HTTP API with interactive docs (Scalar UI), compression, rate limiting
- Optional broker-managed local service for repeated local HTTP solves
- Internal `conda --solver=presto` backend with cached final-state solves through that local service
- GitHub Action for CI pipelines (local CLI and hosted API modes)
- Docker images for server and CLI deployment
- Uses `conda-rattler-solver` for fast SAT solving

## Quick start

```bash
pixi global install --git https://github.com/jezdez/conda-presto.git
conda presto -c conda-forge -p linux-64 python=3.12 numpy
```

## Run a server

```bash
docker run --rm -p 8000:8000 ghcr.io/jezdez/conda-presto:latest
curl http://localhost:8000/health
```

Pin a versioned image tag for deployments. See the [container configuration reference](https://jezdez.github.io/conda-presto/reference/configuration/) for server tuning and Redis result-cache setup.

The server image runs HTTP solves through one persistent worker. The worker
retains loaded repodata and indexes between requests. The container does not
start conda-broker.

## Run a broker-managed local service

conda-presto registers a manual, loopback-only service with conda-broker. It
does not start automatically or change normal `conda presto` commands. See the
[broker-managed local service tutorial](https://jezdez.github.io/conda-presto/tutorials/broker-service/)
to start the service and find its endpoint.

The service can also back the internal `conda --solver=presto` backend. It
remains loopback-only and is not available through the Docker server image. See
the [Presto solver tutorial](https://jezdez.github.io/conda-presto/tutorials/solver-backend/).

## Documentation

Full documentation is available at the [conda-presto docs site](https://jezdez.github.io/conda-presto/):

- [Quick start](https://jezdez.github.io/conda-presto/quickstart/) — install and first resolve
- [CLI tutorial](https://jezdez.github.io/conda-presto/tutorials/cli-resolve/) — in-depth CLI usage
- [HTTP API tutorial](https://jezdez.github.io/conda-presto/tutorials/http-api/) — HTTP workflows
- [CI pipeline](https://jezdez.github.io/conda-presto/tutorials/ci-pipeline/) — GitHub Action setup
- [Reference](https://jezdez.github.io/conda-presto/reference/) — CLI flags, endpoints, formats, env vars
- [Architecture](https://jezdez.github.io/conda-presto/explanation/architecture/) — how it works
- [Roadmap](https://jezdez.github.io/conda-presto/proposals/) — shipped foundations and linked future work

## Development

```bash
git clone https://github.com/jezdez/conda-presto.git
cd conda-presto
pixi install
pixi run lint        # ruff check
pixi run format      # ruff format
pixi run test        # pytest
pixi run bench       # pytest-benchmark
pixi run serve       # uvicorn with --reload
pixi run -e docs docs  # build documentation
```

## License

BSD-3-Clause
