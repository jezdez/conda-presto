# GitHub Action demo

`action.sh` starts a local Presto service and runs the actual `remote` and `output` Bash scripts read from the repository's `action.yml`. It supplies their GitHub input and output environment variables, checks `solved=true`, and validates that the saved lock contains `zlib` for `test/linux-64` and `test/osx-arm64`.

This is local execution of the composite Action client. It does not emulate a GitHub runner, publish an artifact or prove that a hosted workflow passed.

For a hosted run, copy `conda.toml` into a repository and `workflow.yml` to `.github/workflows/presto.yml`. Set the repository variable `CONDA_PRESTO_URL` to your service's HTTPS base URL. The sample uses `main`, which must include workspace support. Pin a reviewed commit for a production workflow.

The Action sends the manifest to that service and uploads the successful saved lock with GitHub's artifact action. It does not install conda or start a solver on the runner.
