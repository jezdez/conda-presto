# conda-presto

A fast, dry-run conda solver exposed as both a CLI and an HTTP API.
Given package specs or an environment file (`environment.yml`,
`pixi.toml`, `pyproject.toml`, `requirements.txt`, conda-lock,
pixi-lock, …), it resolves fully pinned packages for one or more
platforms — without downloading or installing anything — and emits
the result as native JSON or any conda exporter format
(`pixi.lock`, `conda-lock.yml`, environment YAML, explicit file, …).

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
