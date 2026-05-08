# permalink: Permalink solves — content-addressed result cache

Status: proposal, not yet implemented
Owner: TBD
Filed: 2026-04-16

## TL;DR

`POST /resolve` returns a `Location: /r/<sha256>` header alongside the
body. `GET /r/<sha256>` returns the cached result with strong cache
headers. Same input → same URL, forever (until cache eviction).

## Motivation

- **Shareable solves.** Paste a URL into Slack, the receiver sees
  the exact same resolved env you saw — without having to ship a
  lockfile. "Reproducible by URL."
- **HTTP-cache friendly.** GETs are cacheable by CDN intermediaries.
  A high-traffic deployment goes from "solver per request" to
  "solver per *unique* request."
- **Free dedupe across tenants.** Two users requesting the same
  thing share a result.
- **Operational polish.** Pairs naturally with the existing
  repodata-cache warmup story: now the *solve result* cache is
  also long-lived and addressable.

## API surface

```
POST /resolve?spec=numpy&format=conda-lock-v1
  → 200 OK
    Location: /r/3a7f...e91b
    Cache-Control: public, max-age=86400, immutable
    body: <lockfile>

GET /r/3a7f...e91b
  → 200 OK
    Cache-Control: public, max-age=86400, immutable
    Content-Type: application/yaml
    body: <lockfile>

GET /r/3a7f...e91b (cache miss)
  → 404 Not Found
    { "error": "result not in cache; re-POST to recompute" }
```

The hash is `sha256(canonical_json(request))`. Canonicalization:

- Sort spec lists.
- Normalize whitespace in inline YAML/TOML bodies.
- Include channels (sorted), platforms (sorted), format, solve mode,
  and the solver backend version (so a solver upgrade invalidates
  cached results automatically).

## Implementation outline

1. New module `conda_presto/cache.py` with a small `ResultCache`
   protocol and an in-memory LRU implementation backed by
   `cachetools.LRUCache`. Configurable via env:
   - `CONDA_PRESTO_RESULT_CACHE_MAX_ENTRIES` (default 1000)
   - `CONDA_PRESTO_RESULT_CACHE_MAX_BYTES` (default 256 MiB)
2. In `app.py`'s solve handlers (`resolve_get`, `resolve_post`,
   eventually `/diff`):
   - Compute the request hash before solving.
   - Check the cache; if hit, return the cached body + media type
     directly (and add the `Location` header).
   - If miss, solve, store the result, return with `Location`.
3. New `GET /r/{hash}` handler that looks up by hash and returns the
   cached body or 404.
4. Cache stores `(body: bytes, media_type: str, created_at: datetime)`
   tuples — small, simple, easy to swap out for a Redis backend
   later if needed.
5. Add `X-Cache: HIT|MISS` response header for observability.

## Tests

- Two identical POSTs return the same `Location` and `X-Cache: MISS`
  then `X-Cache: HIT`.
- POSTs differing only in spec order → same hash (canonicalization
  works).
- POSTs differing only in `?solve=` mode → different hashes (don't
  collapse semantically distinct requests).
- GET on a known hash returns the cached body with the right
  media type and immutable cache header.
- GET on an unknown hash returns 404.
- Cache eviction works (set max_entries=2, do 3 distinct solves,
  oldest is gone).

## Effort

~1 day for in-memory LRU. Add ~½ day if we want a Redis backend
in the same PR (probably defer).

## Open questions

- **Should cached results expire?** The default `max-age=86400`
  with `immutable` is conservative but kind of fibs — they're
  immutable for a fixed channel snapshot, but a channel update
  could change what the *request* would resolve to. The hash
  doesn't include channel state, only request shape. Acceptable:
  callers asking for `permalink` semantics get a snapshot of "what
  did this resolve to at the time of the original POST." If you
  want fresh, just POST again.
- **Do we want `?cache=no` to bypass on writes?** Probably yes for
  CI use cases that want to verify the solver isn't drifting.
- **Privacy.** Hash includes inline body content. Two users
  uploading the same `environment.yml` would get the same URL —
  fine for OSS, potentially surprising for private specs. Document
  it; don't try to be clever.

## Out of scope

- Disk / Redis backends — design for swap-out, ship LRU only.
- Negative caching (cache failures too) — interesting but adds
  complexity. Defer.
- Browse / list endpoint for cached results — would leak request
  contents across users. Don't.
- Signed permalinks for private deployments. Trivial to layer on
  top later if anyone wants tenant isolation.
