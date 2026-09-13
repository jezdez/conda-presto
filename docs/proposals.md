# Roadmap

conda-presto makes conda operations callable by other systems through a dependable HTTP service. The next release reduces the interfaces and deployment variants maintained by the project.

## Service scope

- Resolve specs or environment files for explicit target platforms.
- Parse inputs through conda's environment-specifier registry and render outputs through its exporter registry.
- Transcode the currently supported lockfile formats without fetching package archives over HTTP.
- Retain successful outputs for retrieval while their cache entries remain available.
- Keep worker isolation, deadlines, recovery, readiness, channel restrictions and bounded storage.
- Provide one server container, a compact CLI and an Action that calls an explicit HTTP endpoint.

Separate diagnostics, the browser workbench, local conda transaction delegation, recorded-request warming, the Action's local mode and the CLI container are removed from the current scope. Upstream doctor or compare enhancements are not prerequisites for the smaller service.

## Optional provider adapters

The service can expose existing provider capabilities without implementing their formats or cryptography itself:

- `/sbom` uses conda-sboms to describe resolved conda package records, retaining requested roots and producing a valid document for each selected platform.
- `/sign` signs exact outputs produced by this deployment through conda-sigstore. The service constructs the limited statement using its configured identity. It does not endorse arbitrary caller-authored claims.
- `/verify` checks a supplied artifact and bundle against the recipient's expected signer identity and issuer, and reports artifact matching separately from supported statement semantics.

Providers are optional. Missing providers or noninteractive signing credentials must produce clear failures. Generic artifact signatures do not claim to capture the complete inputs or original construction of a solve.

## Deferred work

Detailed construction evidence, a solve provenance profile, attestation retrieval, policy evaluation and admission remain deferred. Their accepted requirements are preserved in {doc}`the design records <adr/0003-simplify-the-service>` and issues [20](https://github.com/jezdez/conda-presto/issues/20), [21](https://github.com/jezdez/conda-presto/issues/21), [22](https://github.com/jezdez/conda-presto/issues/22), [23](https://github.com/jezdez/conda-presto/issues/23) and [24](https://github.com/jezdez/conda-presto/issues/24). They are not a committed delivery sequence for this release.

No new package, upstream diagnostic workflow, lockfile format, advisory scanner, MCP integration or release assembly system is part of this reduction. Future additions need a concrete integration problem and an existing provider of the underlying operation where one is available.

## Completion criteria

The smaller service must retain native and exporter solves, multiple-platform isolation, no-fetch HTTP conversion, correct result retention and worker recovery. A real Action-to-service example must exercise the integration. Independent instances must demonstrate correct execution and shared-result retrieval before horizontal behavior is claimed. Measurements must distinguish metadata state, process reuse, full-result hits and misses.
