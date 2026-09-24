# conda-presto

conda-presto exposes conda operations through an HTTP service so other systems can resolve environments, parse inputs and render lockfiles without installing conda themselves. It selects packages for explicit target platforms without creating or changing an environment.

## Capabilities

- Resolve inline specs or supported environment files through conda-rattler-solver
- Render native JSON or an installed conda exporter format
- Discover and solve named workspace environments and targets, then check, update or extract their saved locks
- Convert supported lockfiles over HTTP without solving or downloading packages
- Generate CycloneDX SBOMs from resolved package records
- Sign retained artifacts and verify supplied artifacts with Sigstore
- Retrieve retained results through `/r/<hash>` with memory, file or Redis storage
- Run isolated solver workers with deadlines, readiness and recovery
- Call the service from the GitHub Action using an explicit endpoint

The package also includes the `conda presto` one-shot CLI and server launcher. Signing is disabled by default and requires an operator identity and trust configuration. See the [service scope](https://jezdez.github.io/conda-presto/proposals/) for the current work and deferred construction-evidence design.

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
python -m pip install 'conda-presto[server]'
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

The server image listens on port 8000 and uses a persistent worker. Call `/resolve` to solve an environment and `/openapi.json` to discover the API. See [Run with Docker](https://jezdez.github.io/conda-presto/how-to/run-with-docker/) for configuration and current-source builds.

## Documentation

- [Quick start](https://jezdez.github.io/conda-presto/quickstart/)
- [HTTP tutorial](https://jezdez.github.io/conda-presto/tutorials/http-api/)
- [Workspace tutorial](https://jezdez.github.io/conda-presto/tutorials/workspaces/)
- [Recorded demos and runnable examples](https://jezdez.github.io/conda-presto/demos/)
- [How-to guides](https://jezdez.github.io/conda-presto/how-to/)
- [API and configuration reference](https://jezdez.github.io/conda-presto/reference/)
- [Architecture and operation](https://jezdez.github.io/conda-presto/explanation/)
- [Changelog](https://jezdez.github.io/conda-presto/changelog/)

## Development

```bash
pixi install -e dev
pixi run lint
pixi run format
pixi run -e test test
pixi run -e dev serve
pixi run -e docs docs
```

See [demos/README.md](demos/README.md) to run the workflow examples or regenerate their recordings.

## License

BSD-3-Clause
