# How conda-presto works

conda-presto is a solve-only bridge around conda. It reads package specs or
environment files, selects package records for one or more platforms, and emits
native JSON or a conda exporter format. Its solve paths do not download package
payloads, create prefixes, or run installation transactions.

The internal `conda --solver=presto` plugin adds a separate local path. It
delegates final-state selection to the broker service, then returns control to
the calling conda process for transaction planning and execution.

## Public resolve flow

```{mermaid}
flowchart LR
    A["Request\n(specs or file)"] --> B["Input adapter\n(conda env-spec registry)"]
    B --> C{"Operation"}
    C -->|"resolve"| D["Rattler solve"]
    C -->|"CLI covered lockfile"| E["Reuse package records"]
    C -->|"HTTP lockfile"| H["Inspect metadata or reject"]
    D --> F["Native JSON or\nconda exporter"]
    E --> F
    H --> G
    F --> G["CLI output or\nHTTP response"]
```

The CLI lockfile conversion path reuses records only when the input is a
lockfile, every requested platform is present, the output is another lockfile
format, and no extra specs or channel overrides require a solve. HTTP parsing
inspects lockfile format and platform metadata but rejects `/resolve`, `/diff`,
`/explain`, or `/transcode` work that would materialize uploaded package URLs.

## Input adapters

File detection and parsing are delegated to conda's environment specifier
registry. The supported adapters cover environment YAML, requirements files,
and the conda-lock and rattler-lock formats supplied by conda-lockfiles. Other
installed plugins can participate when they return conda's `Environment` model
or the multi-platform lockfile interface used by the adapter.

Conda's explicit-file specifier does not expose that multi-platform lockfile
interface, so explicit files are not accepted as input. The explicit exporter
remains available as an output format.

The CLI and HTTP layer turn parsed environment files and inline arguments into
the same spec, channel, and platform inputs. HTTP raw uploads use Litestar's
parsed media type plus an optional filename hint to select the file adapter.
For lockfiles, only the trusted local CLI asks the adapter for package records.

## Review operations

The review endpoints are separate from resolve because each one has a different
evidence boundary.

- `/parse` extracts specs and channels from a file.
- `/preflight` applies deterministic local checks without channel access.
- `/repair` runs a bounded search over supported single-spec relaxations.
- `/diff` compares selected package records for two inputs.
- `/explain` walks dependency chains in one selected package state.

Repair, diff, and explain can invoke the same solver path as resolve. Preflight
cannot establish satisfiability because it deliberately avoids channels and the
solver. See {doc}`environment-review` for the complete model.

## Direct solve engine

Direct CLI and public HTTP solves use `conda-rattler-solver`. conda-presto sets
the target platform and configured target virtual-package overrides on conda's
context before building the solver input, including for the host's native
subdir. Other effective virtual-package plugin detections and overrides can
also participate. The direct engine is fixed to the rattler backend.

Multi-platform requests dispatch one solve per platform. A process pool keeps
platform work isolated from conda's process-global context. Persistent server
modes also retain loaded repodata and solver indexes between requests.

The native output path converts selected records into lightweight msgspec
structures. The exporter path keeps conda `Environment` objects because conda
exporter plugins consume that model.

## Internal solver delegation

```{mermaid}
sequenceDiagram
    participant C as Calling conda
    participant B as conda-broker
    participant P as conda-presto service
    C->>B: Discover ready conda-presto.server
    C->>P: POST private solver state to /solver/v1
    P->>P: Check final-state cache or solve with rattler
    P-->>C: Return final package records
    C->>C: Compute and execute local transaction
```

The solver client serializes the state that affects final package selection,
including installed records, history, pins, requested changes, channel order,
settings, and the caller's effective virtual packages. It omits the prefix path
and file inventory.

The service reconstructs the rattler input state and returns package records.
Only the broker-managed loopback service enables this private route. The public
server and Docker image do not.

## Cache and process ownership

The HTTP application owns the bounded result cache and any configured Litestar
file or Redis store. Its foreground solver resources start before the optional
scheduled refresh task and stop after that task has drained.

The broker service owns one persistent foreground worker. Scheduled refresh
creates a separate worker only when a cycle needs to recompute a recorded
request, reuses it within that cycle, and stops it at the end of the cycle.

The Docker server also owns one persistent foreground worker, but it restarts a
failed worker itself and never starts the broker-only scheduler.

See {doc}`caching-and-freshness` for the freshness model and
{doc}`deployment-models` for mode boundaries.

## Plugin integration

conda-presto registers three integrations:

- `conda presto` through conda's subcommand plugin hook
- `presto` through conda's solver plugin hook
- `conda-presto.server` through conda-broker's service provider hook

It also consumes conda's environment specifier and exporter registries. New
installed parser and exporter plugins become available without adding a
conda-presto-specific format implementation.
