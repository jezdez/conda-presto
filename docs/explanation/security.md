# Security and trust model

The `conda presto` command and HTTP API are intentionally solve-only. They read
environment inputs, resolve package metadata, and emit results without creating
prefixes, linking package files, running activation scripts, or installing
packages.

The optional internal `conda --solver=presto` plugin delegates only final-state
solving to the broker service. A normal conda command without `--dry-run` can
then download packages and mutate its prefix through conda's local transaction
code. The service never executes that transaction.

This keeps the service trust boundary narrow: it can answer "what would this
solve to?", while conda or another downstream installer remains responsible for
deciding whether and how to install the result.

## Current controls

Input handling
: raw HTTP file uploads are written to a temporary directory with an allowed
  extension, and client-provided filenames are reduced to their basename before
  use. Parsing is delegated to conda's env-spec plugin registry, matching the
  CLI path.

Error handling
: solver failures expose detailed messages only for known conda error types
  such as unsatisfiable specs or missing packages. Unexpected exceptions are
  logged server-side and returned to clients as a generic internal solver
  error.

Request limits
: the server caps request body size, specs per request, platforms per request,
  and solve duration. These limits protect the service from accidental or
  abusive large solves.

Rate limiting and CORS
: rate limiting is enabled by default per client IP. CORS is disabled unless
  `CONDA_PRESTO_CORS_ORIGINS` explicitly lists the expected frontend origins.

Access logging
: public access logs contain the request path, method, content type, and
  response status. They omit headers, cookies, query parameters, and bodies.
  The private solver route is excluded entirely. The convenience server also
  disables uvicorn's separate access log.

Dry-run package access
: conda-presto reads channel metadata and package records but does not download
  or extract package payloads as part of a solve.

## Broker-managed local service boundary

The broker-managed service listens on a loopback TCP address. Loopback limits
network access, but it does not authenticate the calling operating-system user.
Run the service only on a trusted single-user host or behind equivalent local
process isolation.

When the service uses private channels, keep its credentials, repodata, and
persistent result cache inside that same trust boundary. conda-broker manages
the service lifecycle. It does not add authentication to conda-presto's HTTP API.

The private solver request contains installed records, history, pins, effective
virtual packages, channel definitions, and requested changes. It does not send
the prefix path or prefix file inventory. The client disables environment proxy
handling and redirects, and both sides require a loopback connection.

## Docker boundary

The server image exposes only the public API. It does not start conda-broker,
enable `/solver/v1`, or run scheduled solver-cache refresh. Binding a published
container port to `127.0.0.1` keeps it local to the Docker host. Publishing it
on all interfaces expands the trust boundary and requires the same reverse
proxy, TLS, and access-control decisions as any other HTTP deployment.

The persistent server worker and its parent share conda's package cache. The
server image therefore keeps conda filesystem locking enabled.

## Result cache boundary

HTTP `/resolve` responses can be stored under content-addressed `/r/<hash>`
permalinks. A new resolve request checks metadata freshness before reuse.
Fetching an existing permalink returns that stored snapshot without repeating
the freshness check. A newer resolve can therefore use another address while
the older value remains available until eviction or store removal.

The shared cache is currently appropriate for public-channel solves. Avoid
using a shared public deployment for private channels or credential-bearing
channel URLs. If private channel support is needed before a dedicated policy is
implemented, run an isolated server and avoid sharing its persistent cache
outside that trust domain.

The broker-only solver stores final states under private `solver-v1:` keys.
There is no HTTP route for retrieving them. Those values can still contain
package metadata from private channels. Optional candidate persistence stores
serialized solver requests, but excludes requests with detected channel
credentials or tokenized URLs. Treat the file or Redis store as service data
and keep it inside the deployment trust domain.

The exact cache identities, freshness rules, and persistence failure behavior
are documented in {doc}`../reference/cache`.

## Deployment responsibility

conda-presto does not terminate TLS or authenticate callers. CORS limits which
browsers can read responses, and rate limiting bounds request volume per
client address. Neither is an access-control mechanism.

A network deployment therefore needs an explicit admission boundary. A reverse
proxy can supply TLS and authentication, but the application must trust only
that proxy's forwarded client addresses. Cache storage and channel credentials
must remain inside the same trust domain as the service that uses them.

Follow {doc}`../how-to/deploy-securely` for a production configuration and
go-live checklist.

## Future trust work

Signed solve provenance, attestation serving, policy evaluation, and CEP-aligned
predicate design are tracked as roadmap issues. Those features should make
solved results verifiable by downstream tools instead of inventing a separate
installation path.

See {doc}`../proposals` for the current issue links,
{doc}`deployment-models` for the execution boundaries of each interface, and
{doc}`../how-to/deploy-securely` for deployment steps.
