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
    C -->|"covered lockfile transcode"| E["Render in parser process"]
    C -->|"HTTP review lockfile"| H["Inspect metadata or reject"]
    D --> F["Native JSON or\nconda exporter"]
    E --> F
    H --> G
    F --> G["CLI output or\nHTTP response"]
```

The CLI and `/transcode` lockfile conversion paths load records only when the
input is a lockfile, every requested platform is present, the output is another
lockfile format, and no extra specs or channel overrides require a solve. The
HTTP path uses a concrete conda-presto compatibility adapter to reconstruct and
serialize the lockfile atomically inside the isolated parser process. Temporary
package records do not cross the process boundary, and package archives are not
fetched. Other HTTP operations inspect lockfile format and platform metadata but
reject work that needs those records. Transcoding rejects source structures
that would change selected package identity, dependency constraints, features,
or the supported environment set.

## Input adapters

File detection and parsing are delegated to conda's environment specifier
registry. The supported adapters cover environment YAML, requirements files,
and the conda-lock and rattler-lock formats supplied by conda-lockfiles. Other
installed plugins can participate in normal input handling when they return
conda's `Environment` model.

HTTP transcoding currently adds one project-owned compatibility boundary for
the exact conda-lock v1 and rattler-lock v6 adapters. It returns serialized
content without exposing temporary records. Once conda-lockfiles releases its
atomic transcode method, this boundary can be replaced without changing the
parser-process or HTTP contract.

Conda's explicit-file specifier does not expose that multi-platform lockfile
interface, so explicit files are not accepted as input. The explicit exporter
remains available as an output format.

The CLI and HTTP layer turn parsed environment files and inline arguments into
the same spec, channel, and platform inputs. HTTP raw uploads use Litestar's
parsed media type plus an optional filename hint to select the file adapter.
The CLI uses the adapter's normal record path. `/transcode` uses the isolated
compatibility path to render one of conda-lockfiles' registered formats without
fetching. Other HTTP operations do not request records from an uploaded
lockfile.

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
