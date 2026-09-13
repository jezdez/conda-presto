# Roadmap

conda-presto makes conda capabilities easy to call from other systems. Development focuses on reliable HTTP integration and growing service capacity with additional instances.

The next milestone is service operation under representative concurrent load. Existing capabilities, possible upstream contributions and historical proposals are listed separately below. Historical proposals carry no delivery commitment or accepted design requirements.

## Next milestone: a dependable shared service

The current CI exercises the GitHub Action against a real service. It also runs two independent HTTP instances with separate conda metadata caches and shared Redis, checks retained-result retrieval after the producing instance exits, and reports durations for concurrent uncached work on one and two instances. These checks establish a useful baseline, without demonstrating representative scaling gains.

Build on those checks:

1. **Measure capacity with realistic dependency solves.** Compare one and two replicas using the same workload, channel content and target platforms. Record throughput, latency distribution, errors, CPU and memory. Distinguish cold startup, uncached solves, persistent index reuse and retained-output retrieval.
2. **Exercise failure during active requests.** Stop a replica while requests are in flight through the chosen client or routing setup. Record affected requests, timeouts and errors, continued service and recovery. Keep the existing checks for retrieval after producer shutdown.
3. **Make the deployment reproducible for callers.** Extend the existing HTTP and Action examples with the deployment configuration, workload, observed limits and failure behavior. Use the measurements to choose improvements to workers, request handling or storage.

Completion means a repeatable deployment exercise and recorded results that explain the capacity and failure behavior a caller can expect. Shared Redis alone does not imply shared solve-cache hits: request keys can differ between hosts, while retained artifacts can be retrieved through another instance.

See {doc}`how-to/benchmark-performance`, {doc}`explanation/performance` and {doc}`reference/cache`. This milestone does not require restoring local acceleration, a browser workbench or additional diagnostic endpoints.

## Existing service capabilities

Keep HTTP solving, explicit target platforms, input parsing, registered exporters, request limits, worker isolation and recovery as the service foundation. A small CLI supports local execution and server startup.

| Capability | Current scope |
|---|---|
| {doc}`Lockfile transcoding <proposals/capability/transcoder>` | Supported conversions without solving or fetching package archives over HTTP |
| {doc}`GitHub Action <proposals/integration/github-action>` | A solve client using an explicitly configured HTTP endpoint |
| {doc}`Retained output URLs <proposals/integration/permalink>` | Exact saved bytes available while their entries are retained |
| {doc}`SBOM, signing and verification <reference/http-api>` | Optional adapters over conda-sboms and conda-sigstore |

Conda's environment-specifier and exporter registries own parsing and rendering. Conda-lockfiles owns conversion semantics. Replace Presto's temporary conversion adapter when an upstream API can preserve the required format behavior and avoid package downloads. That maintenance work does not expand format support.

SBOM rendering and cryptographic operations stay with their providers. Callers choose expected signing identities and save artifacts and bundles for their own retention needs. The {doc}`HTTP tutorial <tutorials/http-api>` covers the current workflow.

## Upstream opportunities

These are possible contributions to existing projects, outside Presto's delivery plan. Establish a concrete consumer and a reusable provider operation before considering a Presto HTTP adapter. Presto would own remote execution, limits and response delivery.

| Opportunity | Proposed owner and remaining work |
|---|---|
| {doc}`Input diagnostics and preflight <proposals/capability/preflight>` | Conda environment specifiers and doctor, with support for prospective inputs where needed |
| {doc}`Failed-solve diagnosis and repair <proposals/capability/why-not>` | Conda, doctor and the solver, separating supported failure information from checked repair suggestions |
| {doc}`Environment and lockfile comparison <proposals/capability/diff>` | Reusable comparison in conda compare, including any required support for two saved lockfiles |
| {doc}`Package inclusion explanations <proposals/capability/explain>` | Reusable conda package-graph and query operations |
| {doc}`MCP integration <proposals/integration/meta-mcp>` | Evaluate conda-meta-mcp as a consumer of the existing HTTP API |

Doctor currently operates on installed environments, and compare uses an installed environment and a specification file. Prospective-input diagnostics and comparison between two saved lockfiles would require upstream enhancements. The removed Presto implementations are historical context, not prerequisites for the service milestone.

## Historical proposals

The restored notes on {doc}`receipts <proposals/trust/receipt>`, {doc}`solve attestations <proposals/trust/attestation>`, {doc}`attestation retrieval <proposals/trust/serving-attestations>`, {doc}`admission <proposals/trust/admit>` and {doc}`a shared solve format <proposals/trust/cep-solve-attestation>` preserve the original questions. They are outside the current delivery plan and do not form a planned sequence.

A future construction-information request needs a specific consumer. Presto could contribute facts from its execution, with instrumentation owned by conda and the solver. Signing remains with conda-sigstore, trust and admission decisions with the recipient, and archival storage with the operator. Standardization should follow working producer and consumer integrations.

```{toctree}
:hidden:
:caption: Existing capabilities

proposals/capability/transcoder
proposals/integration/github-action
proposals/integration/permalink
```

```{toctree}
:hidden:
:caption: Upstream opportunities

Upstream: Input diagnostics <proposals/capability/preflight>
Upstream: Failed solves and repair <proposals/capability/why-not>
Upstream: Package comparison <proposals/capability/diff>
Upstream: Package inclusion <proposals/capability/explain>
Upstream: conda-meta-mcp <proposals/integration/meta-mcp>
```

```{toctree}
:hidden:
:caption: Historical proposals

Historical: Solve receipts <proposals/trust/receipt>
Historical: Solve attestations <proposals/trust/attestation>
Historical: Attestation retrieval <proposals/trust/serving-attestations>
Historical: Policy and admission <proposals/trust/admit>
Historical: Shared solve format <proposals/trust/cep-solve-attestation>
```
