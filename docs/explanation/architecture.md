# Architecture

conda-presto exposes conda operations through a Litestar HTTP application. It accepts proposed environment inputs and returns selected packages or rendered artifacts without creating a prefix.

```{mermaid}
flowchart LR
    Client[HTTP client or Action] --> API[Litestar handlers]
    API --> Parser[Isolated input parser]
    Parser --> Specifiers[Conda environment specifiers]
    Parser --> Workspace[Workspace manifests and locks]
    API --> Cache[Result cache and stores]
    API --> Worker[Isolated solve worker]
    Worker --> Solver[conda-rattler-solver]
    Worker --> WorkspaceSolver[conda-workspaces and conda solver API]
    Worker --> Exporters[Conda exporters]
    API --> Transcode[No-fetch lockfile transcode]
```

`app.py` owns requests, admission limits, deadlines and responses. `inputs.py` delegates ordinary inputs to conda's specifier registry, routes workspace manifests and locks to their adapters, and isolates HTTP uploads. `resolve.py` configures target platforms and virtual packages, loads indexes and runs ordinary solves. Multiple ordinary platforms use separate processes because conda context is process-global.

`workspace.py` parses supported manifests through conda-workspaces, composes selected environments and logical targets, and scopes declared virtual packages around solving and cache inspection. It solves selected targets sequentially through conda-workspaces and conda's solver API. `workspace_lock.py` inspects saved `conda.lock` records, selects exact records for export and SBOM generation, checks companion-manifest consistency, and updates named direct dependencies in one target.

`worker.py` owns persistent worker startup, request dispatch and replacement. Without persistent mode, HTTP uses a terminable process for each uncached solve. Persistent mode reuses one worker and retains indexes from the ordinary solve path between calls. Both worker modes dispatch workspace requests to their workspace adapter. The same solve and exporter code supports the compact CLI.

`exporter.py` discovers and calls conda exporters. Native JSON is Presto's own result representation. `lockfile_transcode.py` provides the restricted no-fetch compatibility operation for HTTP and CLI lock conversion until a suitable conda-lockfiles API is released.

`cache.py` retains response bytes and media types. `storage.py` orders and bounds persistent-store operations. A new resolve validates freshness, while a retained URL returns the stored response. See {doc}`/reference/cache`.

SBOM generation uses the included conda-sboms exporter. `attestation.py` signs and verifies exact artifact bytes using the included Sigstore and conda-sigstore libraries in isolated processes. These adapters reuse provider solvers, SBOM rendering and cryptographic verification.
