# GitHub Action reference

The composite Action calls `POST <endpoint>/resolve`. It requires an explicit service URL, `curl` and `jq`. It does not install a solver on the runner.

## Inputs

| Input | Required | Default | Meaning |
|---|:---:|---|---|
| `endpoint` | Yes | Unset | conda-presto base URL. |
| `file` | No | Unset | Environment or workspace manifest in the workflow workspace. |
| `specs` | No | Unset | Comma-separated package specs. |
| `channels` | No | Unset | Comma-separated channels for ordinary solves. Omission uses file or service defaults. |
| `environments` | No | All workspace environments | Comma-separated workspace environment names. |
| `platforms` | No | Host for ordinary inputs, declared targets for workspaces | Comma-separated conda subdirectories or workspace target names. |
| `format` | No | Unset | Exporter name for the `format` query parameter. |
| `output` | No | Unset | Workspace path for the response body. |

At least one of `file` or `specs` normally supplies input. Ordinary file requirements can be combined with `specs`. Workspace manifests reject additional specs and channel overrides. Comma-separated values are not trimmed. There is no `mode` or `command` input.

The Action sends the complete file and its basename, plus supplied specs, channels, environments and platforms, as JSON. The service receives that data. Omitted channels are no longer replaced with `conda-forge`. The format name is URL encoded separately.

Use `format: conda-workspaces-lock-v1` for one combined workspace lock. Omitting both selectors solves all declared environments and targets. An environment without declared platforms requires an explicit platform selection.

## Requests and failures

The endpoint must use HTTPS, except for `localhost`, `127.0.0.1` and `::1`. Requests disable curl's default configuration and bypass proxies for loopback. The connection timeout is 10 seconds, the total timeout is 300 seconds, and the response cap is 100 MiB. Only HTTP 2xx is accepted.

Without `format`, the response must be a native JSON array. A non-null platform or workspace target error sets `solved` to `false`. An invalid response or HTTP failure fails the Action step. Named exporter output is not interpreted as JSON. Combined workspace locks fail when any selected environment and target cannot be solved.

## Outputs

| Output | Meaning |
|---|---|
| `solved` | `true` after a successful response with no native platform errors. |
| `result` | Successful response text up to 100,000 shell characters, otherwise a short placeholder. |

Set `output` to retain a large response as a file. The Action does not print response bodies or supplied request values. Multiline output uses a generated delimiter.

See {doc}`/how-to/use-github-action` for a workflow and {doc}`http-api` for the request fields.
