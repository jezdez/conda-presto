# Security and trust model

conda-presto is intentionally solve-only. It reads environment inputs, resolves
package metadata, and emits results. It does not create prefixes, link package
files, run activation scripts, or install packages.

That keeps the shipped trust boundary narrow: conda-presto can answer "what
would this solve to?" but a downstream installer is still responsible for
deciding whether that result is trusted enough to install.

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
the service lifecycle; it does not add authentication to conda-presto's HTTP
API.

## Result cache boundary

HTTP `/resolve` responses can be stored under content-addressed `/r/<hash>`
permalinks. The cache key includes the normalized request, output format,
dependency versions, and local repodata cache file markers. Expired repodata
bypasses the stored result, and changed package metadata produces a new key.

The shared cache is currently appropriate for public-channel solves. Avoid
using a shared public deployment for private channels or credential-bearing
channel URLs. If private channel support is needed before a dedicated policy is
implemented, run an isolated server and avoid sharing its persistent cache
outside that trust domain.

## Production deployment

Run the HTTP server behind a reverse proxy that terminates TLS and sets
forwarded client IP headers. Start uvicorn with `--forwarded-allow-ips` so rate
limits are keyed by the real client address.

For public internet deployments, review these variables before exposing the
service:

- `CONDA_PRESTO_RATE_LIMIT`
- `CONDA_PRESTO_CORS_ORIGINS`
- `CONDA_PRESTO_MAX_BODY_BYTES`
- `CONDA_PRESTO_MAX_SPECS`
- `CONDA_PRESTO_MAX_PLATFORMS`
- `CONDA_PRESTO_SOLVE_TIMEOUT_S`
- `CONDA_PRESTO_RESULT_CACHE_BACKEND`

## Future trust work

Signed solve provenance, attestation serving, policy evaluation, and CEP-aligned
predicate design are tracked as roadmap issues. Those features should extend
the current dry-run boundary by making solved results verifiable by downstream
tools instead of inventing a separate installation path.

See the [roadmap](../proposals.md) for the current issue links.
