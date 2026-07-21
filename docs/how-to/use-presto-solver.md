# Use the Presto solver backend

The internal `presto` solver delegates conda's final-state solve to the
loopback-only broker service. Conda still computes the local package
transaction and performs any requested installation or removal.

:::{warning}
Use the backend per command while evaluating it. Do not configure it as a
global default. The protocol depends on the supported conda and
conda-rattler-solver versions and has no direct HTTP compatibility promise.
:::

## Start the required service

The solver does not start conda-broker itself:

```bash
conda broker start conda-presto.server
conda broker wait conda-presto.server --timeout 180
```

See {doc}`run-broker-service` if the provider is not discovered or readiness
does not complete.

## Preview a create transaction

Use `--dry-run` first:

```bash
conda create \
  --dry-run \
  --solver presto \
  --name presto-demo \
  --channel conda-forge \
  python=3.13 numpy
```

The service returns the final package state. Conda turns that state into its
normal unlink and link plan locally.

Without `--dry-run`, conda downloads packages and modifies the selected prefix
after the delegated solve. The broker service never performs that transaction.

## Preview changes to an existing environment

Pass `--solver presto` to supported install, update, and remove commands:

```bash
conda install --dry-run --solver presto --name my-env pandas
conda update --dry-run --solver presto --name my-env numpy
conda remove --dry-run --solver presto --name my-env pandas
```

The request includes installed records, history, pins, virtual packages,
channels, requested specs, and relevant solver settings. It does not include
the local prefix path or file inventory.

`--force-reinstall` remains a local conda transaction choice:

```bash
conda install \
  --dry-run \
  --solver presto \
  --force-reinstall \
  --name my-env \
  numpy
```

## Make a repeated request eligible for refresh

Two successful cacheable foreground requests with the same solve state make
that request eligible for scheduled refresh:

```bash
conda create --dry-run --solver presto --name presto-demo python=3.13
conda create --dry-run --solver presto --name presto-demo python=3.13
```

A repeated dry-run can reuse a final-state cache entry. Timing alone does not
prove a cache hit because repodata checks and local client work still occur.
Use {doc}`refresh-solver-cache` to configure regular refresh and
{doc}`monitor-service` to inspect cycle summaries.

The recorded-request identity also includes the caller's local repodata TTL.
The final-state cache key omits that TTL and applies the captured policy during
its marker check.

A completed transaction normally changes installed records, so a later solve
for that prefix commonly has a different cache identity.

## Recognize unsupported operations

The backend rejects operations that the service cannot reproduce accurately:

- offline mode
- `--update-deps`
- conda-build caller-provided indexes or repodata-subset callbacks

Use another conda solver for those operations. The Presto client does not fall
back silently after it rejects unsupported state.

## Stop the service

```bash
conda broker stop conda-presto.server
```

## Boundaries

The protocol is private and has no compatibility promise for direct HTTP
clients. It accepts only loopback clients and is absent from normal and Docker
server deployments. There is no remote solver URL setting.

See {doc}`../reference/solver-backend` for supported semantics, cache identity,
and protocol limits.
