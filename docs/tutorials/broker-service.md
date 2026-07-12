# Run a broker-managed local service

Use conda-broker to run a persistent conda-presto HTTP service on a loopback
port. Its worker retains loaded repodata and indexes between requests. The
service is opt-in: ordinary `conda presto` commands continue to solve in their
own process.

## Install the integration

Use a conda-presto environment with the server dependencies installed. In a
source checkout, Pixi supplies them:

```bash
pixi install
```

conda-broker is installed with conda-presto.

## Start and wait for the service

The service starts only when requested. Start it, then wait for `/health` to
report ready:

```bash
conda broker start conda-presto.server
conda broker wait conda-presto.server --timeout 180
conda broker endpoint conda-presto.server
```

The reported endpoint is the API root. Use it with the normal HTTP API:

```bash
curl -X POST http://127.0.0.1:PORT/resolve \
  -H 'content-type: application/json' \
  -d '{"specs": ["python=3.13"], "platforms": ["linux-64"]}'
```

`wait` finishes only after the service's solver worker has loaded indexes for
the configured channels and platforms. It runs on a broker-assigned loopback
port and disables rate limiting only for that child process. The longer timeout
allows for an initial repodata download when the cache is empty.

## Stop the service

Stop the service when the local workflow is complete:

```bash
conda broker stop conda-presto.server
```

The broker service uses the normal conda-presto result-cache configuration. It
does not create a separate cache location, start automatically, or change the
behavior of one-shot CLI solves.
