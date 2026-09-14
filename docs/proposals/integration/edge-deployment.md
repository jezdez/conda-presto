# Edge deployment

Status: experimental Python Cloudflare adapter implemented and verified locally with two native containers. Hosted deployment and geographic execution have not been demonstrated. Portable shared solve lookup remains unimplemented.

Presto should run across an edge network using distributed native computation and immutable object storage. It consumes published conda metadata, executes conda operations and publishes solved outputs independently of the container that produced them.

## Deployment model

| Primitive | Presto's use |
|---|---|
| Edge request handling | Route the existing HTTP API and deliver cacheable retained outputs |
| Distributed native containers | Run CPython, conda, rattler, registered parsers and exporters with the existing process isolation |
| Immutable object storage | Retain exact outputs independently of their producing container and consume published channel metadata |
| Durable coordination where needed | Track completion, retry interrupted work or deduplicate identical requests |

The Python adapter in `deploy/cloudflare` maps these operations to Cloudflare Workers, Containers, R2 and Durable Objects. A Python Durable Object uses `ctx.container` directly to manage each native instance. The adapter uses a fixed two-instance native pool and publishes eligible retained outputs to R2 before returning their URLs. {doc}`../../how-to/deploy-on-cloudflare` describes local execution and account deployment. Provider-specific integration stays in deployment code.

The adapter keeps synchronous requests and the native Presto image. Its location hints are best effort and do not prove geographic distribution. Running the complete conda stack inside a Worker isolate would require separate work on native dependencies, process execution and metadata access. Asynchronous jobs are a later decision if measured request duration or provider limits require them.

Edge entry points do not establish where solving happens. Cloudflare's [container lifecycle](https://developers.cloudflare.com/containers/concepts/architecture/) and [routing model](https://developers.cloudflare.com/containers/configuration/scaling-and-routing/) require attention to instance identity, placement and cold starts. Container disk is ephemeral, so metadata loading and in-memory index reuse must be measured after restart.

## Metadata and output identity

Start with an immutable metadata snapshot exposed through ordinary conda channel URLs. Shared solve lookup needs a portable identity for the metadata actually consumed, including shards and any fallback repodata. Define a representation compatible with conda's metadata consumers.

Retain the effective request, ordered channels, target platforms, virtual packages, solver settings, dependency versions and exporter identity in the lookup. For mutable channels, preserve freshness checks. A metadata snapshot identifier alone does not identify a solve.

Current request keys include local metadata timestamps, device numbers and inodes. Those markers can differ between instances. Replacing them is separate from {doc}`retained-output identity <permalink>`, which already identifies exact bytes and their media type. Object publication must complete before advertising a retrievable result URL, and the deployment must define retention and eviction behavior.

## Deployment exercise

Use the same representative dependency workload, immutable metadata, target platforms and runtime configuration for each measurement:

1. Route the existing HTTP and Action clients to independent native containers in two geographic locations. Record container identity and execution location for uncached solves, with correct native and exporter results.
2. Retrieve an output through another instance after its producing container stops. Exercise restart and failure during active requests, recording errors, deadlines and recovery.
3. Compare cold startup, metadata transfer, uncached solves, warm index reuse and retained-output retrieval. Record latency distributions, throughput, errors, CPU, memory and cost. Demonstrate shared solve lookup separately when portable metadata identity is implemented.

The local Cloudflare integration check exercises a fixed metadata snapshot in two native containers, validates native and exported outputs, and retrieves retained bytes after both containers stop. The edge CI workflow runs this check alongside Ruff, tests in workerd and a deployment dry run. Existing CI also covers Action-to-service calls and independent HTTP processes with shared Redis. A separate local check also solved against conda-forge over HTTPS. Hosted geographic execution and portable shared solve lookup still need validation or implementation.

Publish the reproducible configuration, workload and results with the existing HTTP and Action examples. Use the results to choose a provider, routing and warm-instance policy, object-store integration and any necessary durable coordination. Independent solves should remain parallel.

See {doc}`../../how-to/benchmark-performance`, {doc}`../../explanation/performance` and {doc}`../../reference/cache` for current measurements and cache behavior.
