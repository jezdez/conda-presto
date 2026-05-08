# diff: `POST /diff` — environment / lockfile diff

Status: proposal, not yet implemented
Owner: TBD
Filed: 2026-04-16
Depends on: [transcoder](transcoder.md)

## TL;DR

Take two environments (any combination of inline specs, env files,
or lockfiles), return the resolved package diff: added, removed,
version-changed, with old → new transitions and per-package
metadata.

## Motivation

- **Universal pain.** Every PR that bumps a conda dep needs "what
  does this actually change in the resolved env?" Today: eyeballing
  lockfile diffs in GitHub. Painful, error-prone, dominated by
  irrelevant hash churn.
- **No conda equivalent exists.** pip has `pip-compile --upgrade`
  with primitive diffs; conda has nothing structured.
- **Composes directly on the transcoder ([transcoder](transcoder.md)).** Implementation:
  transcode each side to a canonical resolved-package list, diff.
  All the parsing and (optional) solving is already done.
- **Killer integration**: a GitHub Action ([github-action](../integration/github-action.md)) that posts the
  diff as a PR comment.

## API surface

```
POST /diff
  Content-Type: application/json
  {
    "from": { ...ResolveRequest... },
    "to":   { ...ResolveRequest... },
    "platforms": [...]              // optional, defaults union of from/to
  }
  →
  {
    "platforms": ["linux-64", "osx-arm64"],
    "diff": {
      "linux-64": {
        "added":   [{name, version, build, channel, url}, ...],
        "removed": [{name, version, build, channel, url}, ...],
        "changed": [{name, from: {version, build}, to: {version, build}, kind: "upgrade|downgrade|build"}],
        "unchanged_count": 187
      },
      "osx-arm64": { ... }
    }
  }
```

`from` and `to` accept the same shapes as `POST /resolve`'s body — any
input format. `?solve=` and `?format=` aren't needed (the diff has its
own canonical shape).

## Implementation outline

1. Resolve both sides through the existing pipeline (parse → optional
   solve → list of `Environment`). Reuse the transcoder fast path
   when both sides are lockfile inputs.
2. For each requested platform, build two `dict[name, Package]` maps
   keyed by package name. The diff is a 3-way comparison:
   - in `to` but not `from` → added
   - in `from` but not `to` → removed
   - in both, but `(version, build)` differs → changed
3. Optional: stable ordering by name within each list. Drop the
   noisy fields from the diff record (skip `sha256`/`md5`/`size` by
   default; keep `url` because it identifies channel + platform).
4. New module `conda_presto/diff.py` with `diff_environments(a, b)`
   pure function. Easy to unit test.

## Tests

- Identical inputs → empty diff
- Bumped dep → one `changed` entry plus expected transitive churn
- Added top-level dep → entries for the dep and its closure
- Multi-platform with same packages but different builds per
  platform → per-platform diff is correct
- Lockfile-vs-lockfile with no platforms in common → 400 with
  helpful error
- Asymmetric inputs (env.yml on the `from`, pixi.lock on the `to`)
  → both get re-solved (or transcoded if both are lockfiles), diff
  is correct

## Effort

~1 day after [transcoder](transcoder.md) lands. The diff data model is the only real
design decision; everything else is reuse.

## Out of scope

- Diff of `requested_packages` (the user-facing top-level deps).
  Useful but a different feature — file as a new plan if needed.
- Semantic version impact summary ("major bump", "minor bump") —
  nice-to-have, defer.
- Patch-format output (npm-style "+numpy 1.26 → 2.0"). Maybe later
  as a `?format=patch` rendering.
