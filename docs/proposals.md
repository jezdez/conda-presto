# Roadmap

conda-presto makes conda capabilities easy to call from other systems. Development focuses on reliable HTTP integration and deployment across an edge network.

The planned architecture combines edge request handling, distributed native computation, immutable object storage and durable coordination where needed. Presto consumes published conda metadata and exposes solving, parsing and artifact operations through its existing HTTP API.

## Next milestone: Presto across an edge network

The experimental Python {doc}`Cloudflare adapter <how-to/deploy-on-cloudflare>` implements the first deployment configuration. Prove the {doc}`edge deployment approach <proposals/integration/edge-deployment>` with the existing synchronous HTTP API and native conda runtime:

1. Run independent Presto containers through an edge entry point without a fixed central Presto server. Record where uncached solves actually execute.
2. Consume the same immutable channel metadata snapshot in both instances and make retained outputs retrievable independently of their producing container. Define portable metadata identity for shared solve lookup.
3. Measure representative concurrent dependency solves, cold startup, metadata loading, warm execution and retained-output retrieval. Exercise failure during active requests and retrieval after a producing instance stops.

Completion means a reproducible deployment exercise with observed execution locations, correct outputs, latency, throughput, resource use, cost and recovery behavior. The adapter has a fixed two-instance pool and shared R2 output retrieval. Hosted geographic execution and portable shared solve lookup still need evidence and implementation respectively. Provider selection, routing and the metadata identifier representation remain open until the experiment establishes their behavior.

Existing capabilities, possible upstream contributions and historical proposals are listed below. Historical proposals carry no delivery commitment or accepted design requirements.

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
:caption: Edge deployment

proposals/integration/edge-deployment
```

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
