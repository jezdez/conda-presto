# Cache reference

The service retains response bodies and media types in a bounded in-process cache, with an optional file or Redis layer. A retained `/resolve` response includes `Location: /r/<key>`. Successful mutable responses use `Cache-Control: no-store` so a later resolve reaches the freshness check.

## Request lookup and artifact identity

Two kinds of entry share the configured capacity:

| Entry | Storage key | Meaning |
|---|---|---|
| Request lookup | `request-v1:<digest>` | Maps operation inputs and, for solves, metadata state to a retained artifact |
| Artifact | `resolve-v1:<digest>` | Stores exact response bytes and media type for `/r/<digest>` |

The artifact digest is SHA-256 over the UTF-8 media type, a NUL separator and the exact body. It is not the SHA-256 of the body alone. Artifact endpoints report a separate `sha256` when callers need that file digest. Two renders with different bytes, including different SBOM timestamps, receive different artifact keys.

For solves, the request digest covers:

- normalized specs, ordered channels and platforms, and output selector
- exporter callback identity and provider distribution versions
- conda-presto, conda, conda-rattler-solver and py-rattler versions
- effective conda solve settings, pins and target virtual-package records
- metadata source and local repodata file size, timestamps, device and inode

Ordinary input files are parsed before those inputs are assembled. Their raw filenames and content are not direct key fields. Workspace solve keys also include the selected environment/target requirements, effective virtual packages and workspace provider versions. Metadata inspection covers only the channel/subdir combinations used by those targets.

Workspace lock identity includes a digest of the uploaded lock content, saved selections, parser provider versions and the companion manifest content digest and format when supplied. Locked export and SBOM keys combine this identity with the operation and exporter identity, without inspecting current repodata. Update request keys include the lock identity, selected dependency names, target solve identity and effective update settings. Changing uploaded lock or companion-manifest content therefore changes these request identities even when the parsed requirements are equivalent.

Changed local metadata markers can cause another lookup even when a later render produces identical artifact bytes. Independent hosts can have different request keys.

A solve request lookup requires fresh, unchanged metadata markers. Publication requires a safe metadata transition across the solve. Missing metadata files, local file channels and ambiguous shard fallback prevent solve-result retention. Unidentifiable exporter providers also bypass result caching.

## Retrieval and credentials

`/r/<key>` returns the stored body and media type without solving or checking current repodata. Its cache header is `public, max-age=86400, immutable`. Eviction or expiry can remove it, after which retrieval returns HTTP 404. These URLs are retained outputs, not permanent archives.

Detected credentials in channels, specs or output package URLs prevent retention. Stored values that fail credential checks are removed. Persistent storage is trusted, so anyone able to write it can replace results.

## Capacity and expiry

`CONDA_PRESTO_RESULT_CACHE_SIZE` defaults to 256 entries, counting request mappings and artifact entries separately. Set it to `0` to disable memory retention. `CONDA_PRESTO_RESULT_CACHE_MAX_MEMORY_MB` defaults to 64 MiB, with `0` disabling the byte cap. Oversized memory entries can still be retained in a configured persistent store.

| Backend | Selection |
|---|---|
| Memory | Default without persistent settings |
| File | Explicit `file` backend or configured cache directory |
| Redis | Explicit `redis` backend or configured Redis URL |

Without an explicit backend, Redis takes precedence over a file directory. File storage requires a directory. Explicit Redis without a URL uses `redis://localhost:6379/0`. Its default namespace is `conda-presto`.

Persistent operations are ordered and each caller waits up to two seconds. Stored entries expire after 24 hours and encoded values are limited to 64 MiB. Corrupt or incompatible values are removed. File storage attempts expiry cleanup at startup and hourly. A failed store operation does not invalidate a successful solve, but a shared artifact address must only be advertised after its required publication succeeds.

Expiry and per-value limits do not bound total storage. Use a hard filesystem quota or Redis `maxmemory` and an eviction policy. Isolate cache writers with a service account and dedicated directory or namespace.

## Deadlines

The solve deadline includes capacity waiting. Metadata inspection changes process-global conda context and cannot be abandoned safely. A blocked inspection can extend observed latency beyond the configured deadline. Apply process or container resource limits as well.

See {doc}`environment-variables` for settings and {doc}`/how-to/configure-result-cache` for setup.
