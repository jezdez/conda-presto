# GitHub Action example

For a hosted run, copy `conda.toml` into a repository and `workflow.yml` to `.github/workflows/presto.yml`. Set the repository variable `CONDA_PRESTO_URL` to your service's HTTPS base URL. The sample uses `main`, which must include workspace support. Pin a reviewed commit for a production workflow.

The Action sends the manifest to that service and uploads the successful saved lock with GitHub's artifact action. It does not install conda or start a solver on the runner.

The repository's Action smoke workflow uses the same manifest to check the hosted client against a local service.
