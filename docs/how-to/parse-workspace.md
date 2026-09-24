# Parse and solve a workspace manifest

Watch the {ref}`demo-workspace` demo and follow the commands below.

Discover the environments in a workspace, inspect their requirements and
solve selected targets into a combined lock. Parse mode reads declarations
without solving. Workspace parsing requires conda-workspaces 0.10.0 or later.

## Create a manifest

Save this as `conda.toml`:

```toml
[workspace]
name = "parse-demo"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64"]

[dependencies]
python = "3.13.*"

[target.linux-64.dependencies]
strace = "*"

[feature.test.dependencies]
pytest = ">=8"

[environments]
default = []
test = { features = ["test"] }
```

The `test` environment inherits the top-level Python requirement and adds
pytest. The Linux target also includes strace.

## Discover and select from the CLI

List the environment declarations and their platform mappings:

```bash
conda presto --parse --file conda.toml
```

The JSON response reports `format: "conda-toml"`, the `default` and `test`
environments, and `selected: []`. Discovery does not select the host platform.

Inspect the test environment for Linux:

```bash
conda presto --parse --file conda.toml \
  --environment test --platform linux-64
```

The `selected` entry contains the Python, pytest and strace requirements,
channels and system requirements for that target. Repeat `--environment` or
`--platform` to select more targets. Selecting only `--environment test`
returns both declared platforms, with strace only in the Linux requirements.

## Use the HTTP API

Start the service in another terminal:

```bash
conda presto --serve
```

These examples use `curl` and `jq`. Submit the same manifest for discovery:

```bash
jq -n --rawfile file conda.toml \
  '{file: $file, filename: "conda.toml"}' |
  curl --fail-with-body --silent --show-error \
    --header 'Content-Type: application/json' --data-binary @- \
    http://127.0.0.1:8000/parse
```

Select the test environment for Linux:

```bash
jq -n --rawfile file conda.toml \
  '{file: $file, filename: "conda.toml", environments: ["test"], platforms: ["linux-64"]}' |
  curl --fail-with-body --silent --show-error \
    --header 'Content-Type: application/json' --data-binary @- \
    http://127.0.0.1:8000/parse
```

The HTTP and CLI responses have the same shape. If either selector is
supplied, omitted environments mean all environments and omitted platforms
mean each selected environment's declared platforms. Supply a platform
explicitly when the manifest declares none. Empty, unknown or ambiguous
selectors produce an error.

## Solve selected environments

Create one lock containing the default and test environments on both platforms:

```bash
conda presto --file conda.toml \
  --environment default --environment test \
  --platform linux-64 --platform osx-arm64 \
  --format conda-workspaces-lock-v1 > conda.lock
```

The lock contains four environment/target solutions. A failed pair prevents
a successful incomplete lock. Omitting both selectors solves all declared
environments and targets.

The HTTP API accepts the same selection through repeated query parameters
when uploading raw TOML:

```bash
curl --fail-with-body --silent --show-error \
  --header 'Content-Type: application/toml' \
  --data-binary @conda.toml \
  'http://127.0.0.1:8000/resolve?filename=conda.toml&environment=default&environment=test&platform=linux-64&platform=osx-arm64&format=conda-workspaces-lock-v1' \
  --output conda.lock
```

JSON requests use `environments` and `platforms` arrays alongside `file`
and `filename`. Successful eligible responses include a `Location` for the
retained exact output. See {doc}`../reference/http-api`.

## Export normalized dependencies

Select one environment to write its composed requirements as a new manifest:

```bash
mkdir -p exported
conda presto --export --file conda.toml --environment test \
  --platform linux-64 --platform osx-arm64 \
  --format conda-toml > exported/conda.toml
```

Export mode composes declarations through conda-workspaces without running the solver. The exporter separates shared and target-specific dependencies. It does not
preserve the original feature organization, comments, tasks or every workspace
setting. This is a normalized dependency manifest, not a fully pinned lock.
The `pixi-toml` and `pyproject-toml` formats provide equivalent output in their
supported syntax. Selected targets must have distinct concrete subdirectories
and identical ordered channels.

The HTTP operation uses the same selection:

```bash
curl --fail-with-body --silent --show-error \
  --header 'Content-Type: application/toml' \
  --data-binary @conda.toml \
  'http://127.0.0.1:8000/export?filename=conda.toml&environment=test&platform=linux-64&platform=osx-arm64&format=conda-toml' \
  --output exported/conda.toml
```

Unsolved declarations cannot produce lockfiles, explicit package URLs or SBOMs. Use the solve operation above when exact package records are required.

## Understand the result

`platform` preserves a workspace target's logical name and `subdir` identifies
its conda platform. This matters when a manifest declares multiple variants
of the same subdirectory. Choose the logical name when the subdirectory is
ambiguous.

Selected local, Git and URL PyPI sources are rejected. Version requirements
that need missing optional conda-pypi support are reported as errors. The
number of selected environment/platform combinations uses the configured
platform limit, with specs and channels limits applied to each target.

Omit the solve format to receive native JSON with an `environment`, logical
`platform`, concrete `subdir`, package list and error field for each target.
Workspace solves use the manifest's requirements and channels, so additional
inline specs, channel overrides and multiple input files are rejected.

`/sbom` rejects direct workspace manifests. After creating `conda.lock`, use
the {doc}`saved-lock SBOM workflow <extract-workspace-lock>` to render selected
environments without solving again. `/capabilities` reports `workspace_parse: true` and
`workspace_solve: true`. See {doc}`../reference/http-api` for response fields,
{doc}`../reference/cli` for options and {doc}`use-github-action` for CI use.
