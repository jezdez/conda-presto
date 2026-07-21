# Output format reference

Without `--format` or `?format=`, conda-presto writes its native JSON result.
Every named format is discovered through conda's environment-exporter plugin
registry. The base dependencies supply conda's built-in exporters and
conda-lockfiles exporters. Other installed plugins can add names and aliases.

## Native JSON

The native response is a JSON array with one `SolveResult` per requested
platform. This is the default for the CLI and both `/resolve` methods.

```json
[
  {
    "platform": "linux-64",
    "packages": [
      {
        "name": "zlib",
        "version": "1.3.2",
        "build": "h25fd6f3_2",
        "build_number": 2,
        "channel": "conda-forge",
        "subdir": "linux-64",
        "url": "https://conda.anaconda.org/conda-forge/linux-64/zlib-1.3.2-h25fd6f3_2.conda",
        "sha256": "245c9ee8d688e23661b95e3c6dd7272ca936fabc03d423cdb3cdee1bbcf9f2f2",
        "md5": "c2a01a08fc991620a74b32420e97868a",
        "size": 95931,
        "depends": ["__glibc >=2.17,<3.0.a0", "libzlib 1.3.2 h25fd6f3_2"],
        "constrains": [],
        "manager": "conda"
      }
    ],
    "error": null
  }
]
```

The selected versions and hashes depend on current repodata. The fields do not.

`platform`
: Target conda subdirectory.

`packages`
: Selected package records. Each record contains `name`, `version`, `build`,
  `build_number`, `channel`, `subdir`, `url`, `sha256`, `md5`, `size`,
  `depends`, `constrains`, and `manager`.

`error`
: `null` after a successful platform solve. A captured solver failure produces
  an empty package list and a safe error string for that platform. Other
  platforms in the same request can still succeed.

## Registered exporter names

With the dependency versions required by this release, these exporters are
registered:

| Primary name | Aliases | Category | Media type | Multiple platforms in one document |
|---|---|---|---|:---:|
| `explicit` | None | Lockfile | `text/plain; charset=utf-8` | No |
| `environment-yaml` | `yaml`, `yml`, `env.yml` | Environment | `application/yaml` | No |
| `environment-json` | `json` | Environment | `application/json` | No |
| `requirements` | `reqs`, `txt` | Environment | `text/plain; charset=utf-8` | No |
| `conda-lock-v1` | `conda-lock` | Lockfile | `application/yaml` | Yes |
| `rattler-lock-v6` | `pixi`, `pixi-lock-v6` | Lockfile | `application/yaml` | Yes |

For an exporter without a multiplatform entry point, conda-presto renders each
platform separately and joins the texts with a newline. Use a single platform
for `environment-json` when the output must be one valid JSON document. The
lockfile exporters with a multiplatform entry point produce one document for
all requested platforms.

An installed plugin can change the available set. Use `GET /formats` to read
the names in the running server.

## `explicit`

Conda's CEP 23 explicit format contains one exact package URL per line after an
`@EXPLICIT` marker. The current conda exporter does not append package hashes
to those URLs.

```bash
conda presto --format explicit -c conda-forge -p linux-64 zlib
```

```text
# This file may be used to create an environment using:
# $ conda create --name <env> --file <this file>
# platform: linux-64
# created-by: conda 26.5.3
@EXPLICIT
https://conda.anaconda.org/conda-forge/linux-64/libzlib-1.3.2-h25fd6f3_2.conda
https://conda.anaconda.org/conda-forge/linux-64/zlib-1.3.2-h25fd6f3_2.conda
```

The `created-by` version follows the installed conda version.

## `environment-yaml`

This exporter writes a conda environment document with pinned package
dependencies selected by the solve.

```bash
conda presto --format environment-yaml -c conda-forge -p linux-64 zlib
```

```yaml
name:
dependencies:
  - libzlib=1.3.2=h25fd6f3_2
  - zlib=1.3.2=h25fd6f3_2
```

The solved `Environment` has no target prefix name or configured channel list,
so those values are absent or null. Exact package URLs retain channel identity.

## `environment-json`

This exporter writes the same conda environment model as JSON.

```bash
conda presto --format environment-json -c conda-forge -p linux-64 zlib
```

```json
{
  "name": null,
  "dependencies": [
    "libzlib=1.3.2=h25fd6f3_2",
    "zlib=1.3.2=h25fd6f3_2"
  ]
}
```

## `requirements`

:::{warning}
Conda registers this CEP 23 MatchSpec exporter, but it requires an
`Environment` with requested packages. conda-presto's solved environments hold
explicit selected package records instead. The current exporter therefore
rejects this format on a normal solve. Use `explicit`, `environment-yaml`, or
`environment-json` for a text representation of a solved environment.
:::

The name remains visible through `GET /formats` because that endpoint reports
the conda registry, not a conda-presto allowlist.

## `conda-lock-v1`

The conda-lockfiles plugin writes one multi-platform `conda-lock.yml` document.

```bash
conda presto --format conda-lock-v1 \
  -c conda-forge \
  -p linux-64 \
  -p osx-arm64 \
  zlib > conda-lock.yml
```

The document records exact package URLs and hashes for each platform. Its
metadata identifies the conda-lockfiles version that created it.

## `rattler-lock-v6`

The conda-lockfiles plugin writes one multi-platform rattler-lock v6 document,
normally named `pixi.lock`.

```bash
conda presto --format pixi-lock-v6 \
  -c conda-forge \
  -p linux-64 \
  -p osx-arm64 \
  zlib > pixi.lock
```

`rattler-lock-v6`, `pixi`, and `pixi-lock-v6` select the same exporter.

## Failure behavior

The exporter path requires every platform solve to succeed. The CLI exits with
status 1 when a solve or exporter fails. HTTP `/resolve?format=<name>` returns
HTTP 500 for those failures because an exporter cannot represent the native
per-platform error shape.

An unknown name returns the sorted registry names in the error. Additional
exporter plugins are accepted without conda-presto-specific dispatch code.

## See also

- {doc}`cli`
- {doc}`http-api`
- {doc}`github-action`
- {doc}`../how-to/transcode-lockfiles`
