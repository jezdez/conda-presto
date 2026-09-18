# Architecture

conda-presto exposes conda operations through a Litestar HTTP application. It accepts proposed environment inputs and returns selected packages or rendered artifacts without creating a prefix.

```{mermaid}
flowchart LR
    Client[HTTP client or Action] --> API[Litestar handlers]
    API --> Parser[Isolated input parser]
    Parser --> Specifiers[Conda environment specifiers]
    API --> Cache[Result cache and stores]
    API --> Worker[Isolated solve worker]
    Worker --> Solver[conda-rattler-solver]
    Worker --> Exporters[Conda exporters]
    API --> Transcode[No-fetch lockfile transcode]
```

`app.py` owns requests, admission limits, deadlines and responses. `inputs.py` delegates parsing to conda's specifier registry and isolates HTTP uploads. `resolve.py` configures explicit target platforms and virtual packages, loads indexes and runs the solver. Multiple platforms use separate processes because conda context is process-global.

`worker.py` owns persistent worker startup, request dispatch and replacement. Ordinary HTTP operation uses a terminable process for each uncached solve. Persistent mode retains loaded indexes between calls. The same solve and exporter code supports the compact CLI.

`exporter.py` discovers and calls conda exporters. Native JSON is Presto's own result representation. `lockfile_transcode.py` is the restricted HTTP compatibility adapter until a suitable conda-lockfiles API is released.

`cache.py` retains response bytes and media types. `storage.py` orders and bounds persistent-store operations. A new resolve validates freshness, while a retained URL returns the stored response. See {doc}`/reference/cache`.

SBOM generation uses the included conda-sboms exporter. Signing and verification use the included Sigstore and conda-sigstore libraries. These adapters do not introduce a second solver, SBOM renderer or cryptographic implementation.
