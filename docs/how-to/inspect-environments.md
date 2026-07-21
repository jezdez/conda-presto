# Inspect and compare environments

Use the inspection endpoints to answer a specific question before accepting a
proposed environment.

| Question | Endpoint | Contacts channels | Runs a solve |
|---|---|:---:|:---:|
| Is the input locally well formed? | `/preflight` | no | no |
| Which simple pin change makes this request feasible? | `/repair` | yes | yes |
| Which packages change between two inputs? | `/diff` | when needed | when needed |
| Why is this package present? | `/explain` | when needed | when needed |
| Which specs and channels does this file declare? | `/parse` | no | no |

Set the server URL before following the examples:

```bash
export CONDA_PRESTO_URL=http://127.0.0.1:8000
```

## Check an input locally

Run preflight before spending time on channel access and solving:

```bash
curl --fail --silent --show-error \
  "$CONDA_PRESTO_URL/preflight" \
  --json '{
    "specs": ["python=3.13", "python=3.13"],
    "channels": ["conda-forge"]
  }' \
  | jq
```

An `ok` value of `true` means that no error-severity finding was produced. It
does not mean that the request is satisfiable.

Preflight also accepts raw environment files:

```bash
curl --fail --silent --show-error \
  --data-binary @environment.yml \
  --header 'Content-Type: application/yaml' \
  "$CONDA_PRESTO_URL/preflight?filename=environment.yml" \
  | jq
```

## Ask for bounded repair suggestions

Submit an infeasible inline request to `/repair`:

```bash
curl --fail --silent --show-error \
  "$CONDA_PRESTO_URL/repair?max_suggestions=3&max_attempts=10" \
  --json '{
    "specs": ["python==3.13", "scipy==1.5"],
    "channels": ["conda-forge"],
    "platforms": ["linux-64"]
  }' \
  | tee repair.json \
  | jq
```

Review a returned change before applying it:

```bash
jq '.suggestions[].changes' repair.json
```

Each suggestion changes one spec and has already solved on every requested
platform. The endpoint never modifies the source file. `completion_reason`
reports whether every candidate was tried or a configured limit stopped the
search.

Repair accepts inline specs only. It does not parse files or propose channel
changes.

## Compare two proposed environments

Pass a `from` input, a `to` input, and the platforms to compare:

```bash
curl --fail --silent --show-error \
  "$CONDA_PRESTO_URL/diff" \
  --json '{
    "from": {"specs": ["python=3.12"], "channels": ["conda-forge"]},
    "to": {"specs": ["python=3.13"], "channels": ["conda-forge"]},
    "platforms": ["linux-64"]
  }' \
  | jq '.diff["linux-64"]'
```

If an input lockfile already contains the requested platform, `/diff` reads
its package records directly. Other inputs are solved before comparison.

## Explain one selected package

Choose exactly one platform and name the package to trace:

```bash
curl --fail --silent --show-error \
  "$CONDA_PRESTO_URL/explain" \
  --json '{
    "package": "libzlib",
    "specs": ["python"],
    "channels": ["conda-forge"],
    "platforms": ["linux-64"]
  }' \
  | jq '.chains'
```

Each chain starts at a requested spec and ends at the selected package.
`complete: false` means the bounded local graph could not account for every
edge.

## Extract specs and channels without solving

Use `/parse` when a client needs conda's environment-specifier plugins but not
the preflight findings:

```bash
curl --fail --silent --show-error \
  "$CONDA_PRESTO_URL/parse" \
  --json "$(jq -n --rawfile file environment.yml \
    '{file: $file, filename: "environment.yml"}')" \
  | jq
```

See {doc}`../reference/http-api` for response fields, status codes, and request
limits.
