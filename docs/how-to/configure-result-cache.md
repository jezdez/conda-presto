# Configure result caching

conda-presto caches successful public `/resolve` responses and private Presto
solver final states in one bounded in-process cache. A file or Redis store can
retain entries across service restarts.

Public resolve entries and private solver entries use separate key namespaces.
Only public resolve entries have `/r/<hash>` URLs.

:::{note}
A permalink returns the stored snapshot without a new repodata freshness check.
A new `/resolve` request performs the freshness check and can move to another
address after metadata changes.
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

For conda-broker, stop the broker before exporting the variables so its new
daemon inherits them:

```bash
conda broker stop
export CONDA_PRESTO_RESULT_CACHE_BACKEND=file
export CONDA_PRESTO_RESULT_CACHE_DIR="$HOME/.cache/conda-presto/results"
conda broker start conda-presto.server
conda broker wait conda-presto.server --timeout 180
```

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

The published server image already contains the Redis client. Choose a unique
namespace when deployments with different trust boundaries use the same Redis
instance.

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
- the persistent store timed out or rejected the write

Local `file://` repodata sources are not retained by the private solver cache.

## Plan for invalidation and eviction

Eviction removes older in-process entries when entry or byte limits are
exceeded. Persistent-store retention is managed by the selected backend.

Repodata freshness is separate. `/resolve` keys include repodata cache-file
markers, so changed metadata produces a different public location. The private
solver cache checks stored markers under the caller's effective repodata
policy, including its index-cache setting.

Read {doc}`../reference/cache` for cache identity, retention, and invalidation.
Read {doc}`../explanation/security` before caching results from private
channels.
