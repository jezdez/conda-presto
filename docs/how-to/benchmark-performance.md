# Benchmark performance

Use a controlled profile to determine which part of a conda-presto request is
expensive for your workload. Do not combine cold startup, refreshed repodata,
index reuse, and full result-cache hits into one result.

:::{important}
Run comparisons on the same host against the same channels and repodata state.
A result from another day can represent different package metadata and a
different solve.
:::

## Run the repository benchmarks

From a source checkout, install the test environment and run the benchmark
task:

```bash
pixi install --environment test
pixi run -e test bench
```

The suite measures serialization paths, representative single-platform solves,
and a multi-platform solve. Solve benchmarks can access channel metadata, so
record whether repodata was downloaded or refreshed during the run.

## Select an HTTP profile

Run one profile at a time in an activated installation or a source-checkout
Pixi shell. Restart the server after changing a profile.

`````{tab-set}

````{tab-item} Full result-cache hit
Keep the result cache enabled. Ordinary HTTP mode is sufficient because a hit
skips the isolated solve process.

```bash
export CONDA_PRESTO_RESULT_CACHE_BACKEND=memory
export CONDA_PRESTO_RESULT_CACHE_SIZE=256
export CONDA_PRESTO_PERSISTENT_WORKER=0
```
````

````{tab-item} Ordinary HTTP miss
Disable result retention to measure the isolated child process used by each
uncached ordinary HTTP request.

```bash
export CONDA_PRESTO_RESULT_CACHE_BACKEND=memory
export CONDA_PRESTO_RESULT_CACHE_SIZE=0
export CONDA_PRESTO_PERSISTENT_WORKER=0
```
````

````{tab-item} Persistent-worker miss
Disable result retention while keeping the worker alive. Repeated requests can
reuse its loaded rattler indexes but still run the solve.

```bash
export CONDA_PRESTO_RESULT_CACHE_BACKEND=memory
export CONDA_PRESTO_RESULT_CACHE_SIZE=0
export CONDA_PRESTO_PERSISTENT_WORKER=1
```
````

`````

Keep the requested startup set narrow, disable rate limiting for the local
measurement, and start the server:

```bash
export CONDA_PRESTO_CHANNELS=conda-forge
export CONDA_PRESTO_ALLOWED_CHANNELS=conda-forge
export CONDA_PRESTO_PLATFORMS=linux-64
export CONDA_PRESTO_RATE_LIMIT=0

conda presto --serve --host 127.0.0.1 --port 8000
```

Wait for readiness in another terminal:

```bash
until curl --fail --silent http://127.0.0.1:8000/health > /dev/null
do
  sleep 1
done
```

## Prime the selected state

Send one request before collecting samples:

```bash
curl --fail --silent --show-error \
  --get http://127.0.0.1:8000/resolve \
  --data-urlencode 'spec=python=3.13' \
  --data-urlencode 'spec=numpy' \
  --data-urlencode 'channel=conda-forge' \
  --data-urlencode 'platform=linux-64' \
  --output /dev/null
```

For the full result-cache profile, this creates the entry that later requests
can reuse. For the two miss profiles, it stabilizes the on-disk repodata state.
Persistent-worker startup has already built the configured worker index.

## Collect repeated HTTP timings

Record loopback request time without writing response bodies to disk:

```bash
for run in 1 2 3 4 5 6 7 8 9 10
do
  curl --fail --silent --show-error \
    --get http://127.0.0.1:8000/resolve \
    --data-urlencode 'spec=python=3.13' \
    --data-urlencode 'spec=numpy' \
    --data-urlencode 'channel=conda-forge' \
    --data-urlencode 'platform=linux-64' \
    --output /dev/null \
    --write-out "$run %{time_total}\n"
done | tee timings.txt
```

Calculate the median and range with the standard library:

```bash
python - <<'PY'
from pathlib import Path
from statistics import median

samples = [
    float(line.split()[1])
    for line in Path("timings.txt").read_text().splitlines()
]
print(f"samples: {len(samples)}")
print(f"median: {median(samples):.6f} s")
print(f"range: {min(samples):.6f} to {max(samples):.6f} s")
PY
```

Repeat the procedure for each profile. Treat `GET /r/<hash>` as a separate
operation. It retrieves a stored snapshot without the repodata freshness check
performed by a repeated `/resolve` request.

## Report enough context

Include these details with the result:

- execution mode and process warmth
- package specs, channels, and platforms
- conda, conda-presto, and solver versions
- repodata TTL and whether a refresh occurred
- cache backend and the intended hit or miss profile
- prefix state and virtual-package overrides for solver-backend tests
- median, range, and number of samples

Use {doc}`../explanation/performance` to interpret the cost model and
{doc}`../reference/cache` to distinguish result-cache reuse from index reuse.
