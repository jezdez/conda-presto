# Configure result caching

conda-presto caches successful `/resolve` responses in a bounded in-process cache. A file or Redis store can retain entries across service restarts. Retained entries are available through `/r/<hash>`.

Persistent entries expire after 24 hours. Encoded values larger than 64 MiB are
not stored or loaded. These per-entry controls do not limit aggregate disk or
Redis usage. The persistent backend is a trusted service resource, so only the
conda-presto service account should have write access to its directory or Redis
namespace.

:::{note}
A permalink returns the stored snapshot without a new repodata freshness check.
A new `/resolve` request performs the freshness check. Different output bytes or media types receive different addresses.
:::

## Choose a backend

| Backend | Survives restart | Additional service | Typical use |
|---|:---:|:---:|---|
| memory | no | no | Local development and disposable servers |
| file | yes | no | One service process with durable local storage |
| Redis | yes | yes | Containers or services using shared durable storage |

The in-process entry and byte limits apply with every backend. A persistent
store is checked after an in-process miss.

## Configure the memory cache

Memory is the default backend:

```bash
export CONDA_PRESTO_RESULT_CACHE_BACKEND=memory
export CONDA_PRESTO_RESULT_CACHE_SIZE=256
export CONDA_PRESTO_RESULT_CACHE_MAX_MEMORY_MB=64
conda presto --serve
```

Set the entry count to `0` to disable in-process retention. Set the memory
limit to `0` to remove the byte cap. Negative values are rejected at startup.

## Configure a file-backed cache

Create a directory writable by the service account:

```bash
mkdir -p "$HOME/.cache/conda-presto/results"
chmod 700 "$HOME/.cache/conda-presto/results"

export CONDA_PRESTO_RESULT_CACHE_BACKEND=file
export CONDA_PRESTO_RESULT_CACHE_DIR="$HOME/.cache/conda-presto/results"
conda presto --serve
```

Put this directory on a filesystem or volume with a hard quota sized for the
deployment. The file backend attempts expiry cleanup at startup and once per
hour, but expiry cleanup is best effort and is not an aggregate storage bound.

Use {doc}`run-with-docker` for container volume ownership and mounting.

## Configure Redis

Install the Redis client in non-container conda environments that do not
already provide it:

```bash
conda install --channel conda-forge redis-py
```

Configure the backend and namespace before starting the service:

```bash
export CONDA_PRESTO_RESULT_CACHE_BACKEND=redis
export CONDA_PRESTO_RESULT_CACHE_REDIS_URL=redis://127.0.0.1:6379/0
export CONDA_PRESTO_RESULT_CACHE_REDIS_NAMESPACE=conda-presto
conda presto --serve
```

Configure a hard Redis memory limit and an eviction policy before admitting
traffic. For a Redis instance dedicated to cache data, for example:

```text
maxmemory 1gb
maxmemory-policy allkeys-lru
```

The published server image already contains the Redis client. Choose a unique
namespace when deployments with different access requirements use the same Redis
instance. A namespace separates keys, but Redis applies `maxmemory` and its
eviction policy to the whole instance. Use separate instances when deployments
need independent memory limits or access controls.

## Verify a public cache entry

Send a solve and capture its headers:

```bash
export CONDA_PRESTO_URL=http://127.0.0.1:8000

curl --fail --silent --show-error \
  --dump-header response-headers.txt \
  --output solve-result.json \
  --get "$CONDA_PRESTO_URL/resolve" \
  --data-urlencode 'spec=zlib' \
  --data-urlencode 'channel=conda-forge' \
  --data-urlencode 'platform=linux-64'
```

Extract the relative location and retrieve the stored response:

```bash
LOCATION="$(
  awk '
    tolower($1) == "location:" {
      gsub("\\r", "", $2)
      print $2
    }
  ' response-headers.txt
)"

curl --fail --silent --show-error \
  "$CONDA_PRESTO_URL$LOCATION" \
  --output cached-result.json

cmp solve-result.json cached-result.json
```

With a file or Redis backend, stop and restart the service, then request the
same `/r/<hash>` URL to verify persistent retrieval.

## Understand a missing Location header

A successful response can still be returned without a cache location. Check
these conditions:

- both in-process capacity and persistent storage are unavailable
- the response is larger than the in-process byte limit and no persistent
  backend retained it
- repodata markers were unavailable or changed while solving
- a channel, package spec, or produced package URL contained a detected credential pattern
- the selected exporter provider could not be identified by callback and
  installed distribution version
- the persistent store timed out or rejected the write

Local `file://` repodata sources are not retained.

## Plan for invalidation and eviction

Eviction removes older in-process entries when entry or byte limits are
exceeded. A file-store quota or Redis `maxmemory` and eviction policy provide
the aggregate persistent bound. Entry expiry does not replace that bound.

Repodata freshness is separate. Request lookup keys include local metadata markers. The public result key identifies its exact body and media type, so refreshed metadata alone does not change that key when the output is identical.

Read {doc}`../reference/cache` for cache identity, retention, and invalidation.
Read {doc}`../explanation/security` before caching results from private
channels.
