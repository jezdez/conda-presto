# Benchmark the service

Compare the same specs, channels and platforms against the same metadata. Separate cold startup, uncached solves, persistent index reuse and full-result retrieval.

From a source checkout, `pixi run -e test bench` runs the existing solve and serialization benchmarks. They may access channel metadata. For HTTP measurements, choose one profile and restart the server after changing it:

| Profile | `CONDA_PRESTO_RESULT_CACHE_SIZE` | `CONDA_PRESTO_PERSISTENT_WORKER` |
|---|---:|---:|
| Full result reuse | 256 | 0 |
| Isolated process miss | 0 | 0 |
| Persistent worker miss | 0 | 1 |

Use `CONDA_PRESTO_RESULT_CACHE_BACKEND=memory` so a persistent store cannot supply hidden hits. Keep startup targets narrow and disable rate limiting for the local measurement:

```bash
export CONDA_PRESTO_CHANNELS=conda-forge
export CONDA_PRESTO_ALLOWED_CHANNELS=conda-forge
export CONDA_PRESTO_PLATFORMS=linux-64
export CONDA_PRESTO_RATE_LIMIT=0
conda presto --serve
```

After readiness, send one request to establish the selected cache state. Then record repeated timings:

```bash
for run in 1 2 3 4 5 6 7 8 9 10
do
  curl --fail --silent --show-error \
    --get http://127.0.0.1:8000/resolve \
    --data-urlencode 'spec=zlib' \
    --data-urlencode 'channel=conda-forge' \
    --data-urlencode 'platform=linux-64' \
    --output /dev/null --write-out "$run %{time_total}\n"
done
```

Report the median, range and sample count with component versions, virtual-package overrides, metadata freshness, process state and cache configuration. Measure `/r/<hash>` separately because it skips the freshness check performed by `/resolve`. Compare one-instance and multiple-instance workloads with equal request counts and independent metadata caches before claiming scaling gains.

See {doc}`../explanation/performance` for interpretation.
