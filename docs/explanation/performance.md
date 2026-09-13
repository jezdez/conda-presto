# Performance

The service avoids requiring each integration to install and start conda. Its latency depends on metadata freshness, worker state and whether a complete result is retained.

## Work performed

A miss can include input parsing, repodata download or validation, index construction, SAT solving, export and serialization. A one-shot CLI also pays Python and conda startup costs. A persistent worker retains indexes between requests. Ordinary HTTP uses an isolated process for each uncached solve.

A full result hit skips solving and rendering after validating the request's metadata state. `/r/<hash>` is cheaper still because it retrieves stored bytes without that freshness check. Measure these operations separately.

## Concurrency

Multiple target platforms use separate solve processes. More workers can reduce elapsed time while increasing memory use. The default container serializes foreground requests through one persistent worker, whose internal pool can still solve multiple platforms.

Additional service instances provide independent process state. Shared Redis makes retained outputs available to those instances, but local metadata markers can give equivalent requests different cache keys. One-instance and multiple-instance throughput must be observed on the same workload and channel content. There is no published service-level performance guarantee.

## What to record

Report explicit specs, channels, platforms, component versions, virtual-package overrides, metadata freshness, worker reuse, cache backend, concurrency and sample count. Compare cold startup, misses and retained results separately. Faster rattler solving reduces miss cost, which can narrow the advantage of result reuse.

HTTP transcoding performs no SAT solving or package download and should be measured separately from solves. The CLI uses the input adapter's normal record materialization path.

See {doc}`../how-to/benchmark-performance` for controlled measurements and {doc}`/reference/cache` for cache behavior.
