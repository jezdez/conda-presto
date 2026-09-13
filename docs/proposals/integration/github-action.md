# GitHub Action

Status: available in the current checkout. Original tracking issue: [#17](https://github.com/jezdez/conda-presto/issues/17).

The Action makes environment resolution part of a CI workflow. Keeping it in the service repository lets request handling and workflow inputs evolve together. Teams can use their configured service for shared solving and retain the returned lockfile as a workflow artifact.

The current composite Action calls `POST /resolve` at an explicit `endpoint`. It accepts an environment file or package specs, channel and platform selections, and an optional exporter format. It reports `solved` and `result`, and can write the response to a file. Runners need `curl` and `jq`.

The earlier local installation mode and command selection are no longer available. Additional operations would need an explicit workflow use case and matching service support before adding Action inputs.

See {doc}`../../how-to/use-github-action` for a current workflow and {doc}`../../reference/github-action` for inputs, output limits, and failure behavior.
