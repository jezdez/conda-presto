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
- HTTP API with interactive docs (Scalar UI), compression, rate limiting
- MCP endpoint for AI agent integration
- GitHub Action for CI pipelines (local and remote modes)
- Docker images for server and CLI deployment
- Uses `conda-rattler-solver` for fast SAT solving

## Quick start

```bash
pixi global install --git https://github.com/jezdez/conda-presto.git
conda presto -c conda-forge -p linux-64 python=3.12 numpy
```

## Documentation

Full documentation is available at the [conda-presto docs site](docs/index.md):

- [Quick start](docs/quickstart.md) — install and first resolve
- [CLI tutorial](docs/tutorials/cli-resolve.md) — in-depth CLI usage
- [HTTP API tutorial](docs/tutorials/http-api.md) — HTTP workflows
- [CI pipeline](docs/tutorials/ci-pipeline.md) — GitHub Action setup
- [Reference](docs/reference/cli.md) — CLI flags, endpoints, formats, env vars
- [Architecture](docs/explanation/architecture.md) — how it works
- [Proposals](docs/proposals/index.md) — future feature designs

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
