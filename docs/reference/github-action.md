# GitHub Action reference

The composite Action calls `POST <endpoint>/resolve`. It requires an explicit service URL, `curl` and `jq`. It does not install a solver on the runner.

## Inputs

| Input | Required | Default | Meaning |
|---|:---:|---|---|
| `endpoint` | Yes | Unset | conda-presto base URL. |
| `file` | No | Unset | Environment file in the workflow workspace. |
| `specs` | No | Unset | Comma-separated package specs. |
| `channels` | No | `conda-forge` | Comma-separated channels in priority order. |
| `platforms` | No | Unset | Comma-separated target platforms. |
| `format` | No | Unset | Exporter name for the `format` query parameter. |
| `output` | No | Unset | Workspace path for the response body. |

At least one of `file` or `specs` normally supplies input. When both are present, their specs are combined. Comma-separated values are not trimmed. There is no `mode` or `command` input.

The Action sends the complete file and its basename, plus supplied specs, channels and platforms, as JSON. The service receives that data. The format name is URL encoded separately.

## Requests and failures

The endpoint must use HTTPS, except for `localhost`, `127.0.0.1` and `::1`. Requests disable curl's default configuration and bypass proxies for loopback. The connection timeout is 10 seconds, the total timeout is 300 seconds, and the response cap is 100 MiB. Only HTTP 2xx is accepted.

Without `format`, the response must be a native JSON array. A non-null platform error sets `solved` to `false`. An invalid response or HTTP failure fails the Action step. Named exporter output is not interpreted as JSON.

## Outputs

| Output | Meaning |
|---|---|
| `solved` | `true` after a successful response with no native platform errors. |
| `result` | Successful response text up to 100,000 shell characters, otherwise a short placeholder. |

Set `output` to retain a large response as a file. The Action does not print response bodies or supplied request values. Multiline output uses a generated delimiter.

See {doc}`/how-to/use-github-action` for a workflow and {doc}`http-api` for the request fields.
