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

Local mode downloads Pixi 0.70.1 for the runner platform, verifies its pinned
SHA-256 digest, then executes:

```text
pixi run --manifest-path <action-path>/pyproject.toml -e cli conda-presto <arguments>
```

The checked-out action path supplies the verified installer, manifest, and
conda-presto source. The action does not install conda-presto globally or fetch
its repository a second time.

The local Pixi environment supports `linux-64`, `linux-aarch64`, `osx-64`, and
`osx-arm64`. Windows is not a platform in the workspace manifest.

The action maps `file`, `channels`, `platforms`, `format`, and `specs` to the
corresponding CLI arguments. Standard output and standard error are captured
together. A nonzero CLI exit makes the action fail without copying the captured
body into the workflow log.

## Remote mode

Remote mode requires `endpoint`, `curl`, and `jq`. It sends a JSON object to
`POST <endpoint>/resolve`. Present inputs map to `file`, `filename`, `specs`,
`channels`, and `platforms` body fields. This sends the complete input file to
the configured service. Use only a trusted HTTPS endpoint whose operators are
allowed to read that file and its channel configuration. The filename is the
basename of the input path. `format` is URL encoded as the `format` query
parameter. The action appends `/resolve` after removing a trailing slash from
`endpoint`.

The endpoint must use HTTPS. Plain HTTP is accepted only for `localhost`,
`127.0.0.1`, or `::1`. The request disables curl's default `curlrc`
configuration, permits only HTTP and HTTPS at curl's protocol layer, bypasses
proxies for loopback, and applies connection, total request, and response-size
limits. The connection timeout is 10 seconds, the total timeout is 300 seconds,
and the response cap is 100 MiB. Only HTTP 2xx is accepted.

When `format` is unset, the shared output step requires a top-level JSON array
and checks its platform error fields before setting `solved`. An invalid native
JSON shape fails the Action. Named exporter output is not interpreted as JSON.

## Outputs

| Output | Meaning |
|---|---|
| `solved` | `true` when the operation completed and native JSON contains no non-null platform error. `false` for native partial failures and action failures. Exporter output is considered solved after a successful command or HTTP response. |
| `result` | Successful response text up to 100,000 shell characters. Larger responses use a short placeholder. |

When `output` is set, a successful response is also written to that path.
Response bodies are not printed automatically. Use `output` when the response
is too large for `result`.

The action uses a generated delimiter for multiline GitHub output. It logs
whether request fields were provided, not their values. Solver response bodies
are available through the bounded `result` output or the explicitly requested
output path.

## See also

- {doc}`/how-to/use-github-action`
- {doc}`/reference/cli`
- {doc}`/reference/http-api`
- {doc}`/reference/output-formats`
