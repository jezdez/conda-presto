# GitHub Action reference

The repository root contains a composite action named `conda-presto`. It runs
one resolve operation in either local or remote mode. There is no `command`
input.

## Inputs

| Input | Required | Default | Meaning |
|---|:---:|---|---|
| `mode` | No | `local` | `local` runs the checked-out action's CLI environment. `remote` calls an HTTP server. |
| `file` | No | Unset | Path to an environment file in the workflow workspace. |
| `specs` | No | Unset | Comma-separated package specs. |
| `channels` | No | `conda-forge` | Comma-separated channels in priority order. |
| `platforms` | No | Unset | Comma-separated target platforms. |
| `format` | No | Unset | Conda exporter name passed as `--format` or `?format=`. |
| `output` | No | Unset | Workspace path that receives the response body. |
| `endpoint` | Remote mode | Unset | conda-presto base URL used for `POST <endpoint>/resolve`. |

At least one of `file` or `specs` is normally needed. When both are present,
the CLI or HTTP request combines their specs. The action does not trim
individual comma-separated values.

Only the exact `local` and `remote` mode names select an execution step. Any
other value leaves no response to process and the action fails.

## Local mode

Local mode uses `prefix-dev/setup-pixi`, then executes:

```text
pixi run --manifest-path <action-path>/pyproject.toml -e cli conda-presto <arguments>
```

The checked-out action path supplies both the manifest and conda-presto source.
The action does not install conda-presto globally or fetch its repository a
second time.

The local Pixi environment supports `linux-64`, `linux-aarch64`, `osx-64`, and
`osx-arm64`. Windows is not a platform in the workspace manifest.

The action maps `file`, `channels`, `platforms`, `format`, and `specs` to the
corresponding CLI arguments. Standard output and standard error are captured
together. A nonzero CLI exit makes the action fail after printing the captured
output.

## Remote mode

Remote mode requires `endpoint`, `curl`, and `jq`. It sends a JSON object to
`POST <endpoint>/resolve`. Present inputs map to `file`, `filename`, `specs`,
`channels`, and `platforms` body fields. The filename is the basename of the
input path. `format` is sent as the `format` query parameter. The action appends
`/resolve` without normalizing a trailing slash in `endpoint`.

An HTTP status of 400 or greater makes the action fail. The action emits a
GitHub error annotation, then prints the response as formatted JSON when
possible or as plain text otherwise.

## Outputs

| Output | Meaning |
|---|---|
| `solved` | `true` when the operation completed and native JSON contains no non-null platform error. `false` for native partial failures and action failures. Exporter output is considered solved after a successful command or HTTP response. |
| `result` | Complete response text up to 100,000 shell characters. Larger responses use a short placeholder. |

When `output` is set, the response is also written to that path. Without it,
the response is printed to the workflow log. Responses larger than the action's
100,000-character threshold should use `output`, because the `result` output
does not contain their body.

The action uses a generated delimiter for multiline GitHub output. It logs
whether request fields were provided, not their values. Solver responses and
errors can still appear in the workflow log when no output path is set or when
the operation fails.

## See also

- {doc}`/how-to/use-github-action`
- {doc}`/reference/cli`
- {doc}`/reference/http-api`
- {doc}`/reference/output-formats`
