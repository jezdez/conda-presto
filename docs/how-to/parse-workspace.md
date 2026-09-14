# Parse a workspace manifest

Discover the environments in a workspace and inspect their requirements for
selected platforms. This operation reads declarations without solving or
installing packages. Workspace parsing uses the conda-workspaces revision pinned
in `pyproject.toml` until its parser fixes are released.

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

## Understand the result

`platform` preserves a workspace target's logical name and `subdir` identifies
its conda platform. This matters when a manifest declares multiple variants
of the same subdirectory. Choose the logical name when the subdirectory is
ambiguous.

Selected local, Git and URL PyPI sources are rejected. Version requirements
that need missing optional conda-pypi support are reported as errors. The
number of selected environment/platform combinations uses the configured
platform limit, with specs and channels limits applied to each target.

Workspace solving is not available yet. `/resolve`, `/sbom` and normal CLI
solves reject workspace manifests. `/capabilities` reports
`workspace_parse: true` and `workspace_solve: false`. See
{doc}`../reference/http-api` for response fields and
{doc}`../reference/cli` for parse-mode options.
