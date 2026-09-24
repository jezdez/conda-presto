# Validate a workspace lock against its manifest

Watch the {ref}`demo-workspace` demo or run its checked-in script.

Use a saved workspace `conda.lock` to check whether every declared environment and target still satisfies its manifest. This is useful after changing dependencies or channels and before accepting a lock in CI. Start with the `conda.toml` and combined lock from {doc}`parse-workspace`.

## Check the saved lock

Run the check with both input files:

```bash
conda presto --validate --file conda.lock --manifest conda.toml
```

The standalone `conda-presto` command accepts the same options. The manifest can also be a supported `pixi.toml` or `pyproject.toml`. Keep the saved lock's filename `conda.lock` so the parser selects the workspace lock format.

The JSON report contains an overall `consistent` flag and one result for every environment and logical target. Each result identifies its `environment`, `platform` and concrete conda `subdir`. A consistent target has `consistent: true` and `reason: null`. A mismatch has `consistent: false` and a reason describing the first unmet check. Workspace-wide declaration mismatches can appear in several target results and name another affected environment.

Logical targets remain separate even when they share a concrete platform. The check uses the target's declared virtual packages rather than the host's detected capabilities. Environment and platform selectors are rejected so a passing result covers the whole manifest.

## Detect a manifest change

In the example manifest, change the Python requirement from `3.13.*` to `3.12.*` without regenerating the lock. Repeat the check. It returns a JSON report with mismatching targets and exits with status 1 because the saved Python records no longer satisfy the declaration. Restore the original requirement or regenerate the lock deliberately, then run the check again.

The command leaves both files unchanged. It does not solve, fetch repodata, download archives, install packages or run workspace tasks. Additional package specs and channel overrides are rejected because the comparison uses the supplied manifest's requirements and channels.

## Use the exit status in CI

Save the report while preserving the command's result:

```bash
status=0
conda presto --validate --file conda.lock --manifest conda.toml \
  > lock-check.json || status=$?
cat lock-check.json
exit "$status"
```

Exit status 0 means every target is consistent. Status 1 means the check completed and found mismatches, with details in `lock-check.json`. Status 2 means the inputs could not be checked, for example because they are malformed, unsupported, unreadable or exceed the parser deadline. Those errors go to stderr and do not produce a JSON report.

## What the check covers

Presto calls conda-workspaces' `check_lockfile_satisfiability()` with an environment selector for each declared target. Workspaces compares requirements, channels, package dependencies, constraints and virtual package requirements against the saved records. Conda's `MatchSpec`, channel and package record APIs supply the underlying conda semantics. The environment selector is available in conda-workspaces 0.10.0 and later, required by Presto. No additional provider is required.

Virtual package versions come from the target's system requirements, falling back to Presto's configured operating system baselines. Host CPU and GPU detection is disabled. See {doc}`../reference/environment-variables` for the baseline settings.

Only self-contained conda package records are supported. PyPI dependencies, external package references and `archspec` system requirements fail as unsupported input. A passing result does not establish that packages are current, that downloaded payloads match their metadata or that the environment has no vulnerabilities.

The same operation is available through {doc}`POST /validate <../reference/http-api>` for systems that use Presto remotely.
