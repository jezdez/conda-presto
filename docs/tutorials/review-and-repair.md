# Review and repair an environment

This tutorial treats an environment change as a review sequence. You will check
the input locally, diagnose an infeasible pin, apply a suggested relaxation,
compare two feasible results, and trace one transitive dependency.

## Start a local server

Use the latest-release installation from {doc}`../quickstart`, then run the HTTP
API in one terminal:

```bash
conda presto --serve
```

Set its URL in another terminal:

```bash
export CONDA_PRESTO_URL=http://127.0.0.1:8000
curl -sS "$CONDA_PRESTO_URL/health"
```

## Check input quality

Preflight checks syntax and local policy without contacting channels or running
the solver:

```bash
curl -sS "$CONDA_PRESTO_URL/preflight" \
  --json '{
    "specs": ["python=3.13", "numpy", "numpy"],
    "channels": ["conda-forge"]
  }' | jq
```

The duplicate dependency produces a finding. A successful preflight means the
request is well formed. It does not mean that the request can be solved.

## Ask for a bounded repair

Submit an intentionally unavailable exact version:

```bash
curl -sS "$CONDA_PRESTO_URL/repair?max_suggestions=2" \
  --json '{
    "specs": ["python==99", "numpy"],
    "channels": ["conda-forge"],
    "platforms": ["linux-64"]
  }' > repair.json

jq '{feasible, diagnosis, suggestions, completion_reason}' repair.json
```

Repair considers deterministic, single-spec relaxations. For this request it
can suggest replacing `python==99` with `python`. Every returned suggestion has
already solved on every requested platform.

conda-presto does not apply the change. Copy an acceptable suggestion into a
new request and resolve it normally:

```bash
curl -sS "$CONDA_PRESTO_URL/resolve" \
  --json '{
    "specs": ["python", "numpy"],
    "channels": ["conda-forge"],
    "platforms": ["linux-64"]
  }' | jq '.[0] | {platform, error}'
```

## Compare two feasible revisions

Compare a Python 3.12 baseline with the relaxed request:

```bash
curl -sS "$CONDA_PRESTO_URL/diff" \
  --json '{
    "from": {"specs": ["python=3.12", "numpy"], "channels": ["conda-forge"]},
    "to": {"specs": ["python", "numpy"], "channels": ["conda-forge"]},
    "platforms": ["linux-64"]
  }' | jq '.diff["linux-64"]'
```

The response separates added, removed, and changed packages. Diff reports what
the two resolved environments contain, not whether the change is desirable.

## Trace a transitive dependency

Ask why `libzlib` is present in the selected Python environment:

```bash
curl -sS "$CONDA_PRESTO_URL/explain" \
  --json '{
    "package": "libzlib",
    "specs": ["python"],
    "channels": ["conda-forge"],
    "platforms": ["linux-64"]
  }' | jq
```

Each returned chain starts at a requested package and ends at `libzlib`.
`complete: false` means the bounded local graph could not account for every
edge.

## Finish with an exported result

After accepting the input, write a lockfile from the same request:

```bash
curl -sS "$CONDA_PRESTO_URL/resolve?format=pixi-lock-v6" \
  --json '{
    "specs": ["python", "numpy"],
    "channels": ["conda-forge"],
    "platforms": ["linux-64"]
  }' > pixi.lock
```

## What you learned

- Preflight checks local input quality, not satisfiability.
- Repair returns tested suggestions but never changes the request automatically.
- Diff compares resolved package states.
- Explain traces why one selected package is present.

For task recipes, see {doc}`../how-to/inspect-environments`. The design
boundaries are explained in {doc}`../explanation/environment-review`.
