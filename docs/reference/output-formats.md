# Output formats

conda-presto can emit solve results in several formats. The default is
a structured JSON array. All other formats are provided by conda
exporter plugins (shipped with `conda-lockfiles`) and are selected
with `--format` on the CLI or `?format=` on the HTTP API.

Any additional formats registered by other installed exporter plugins
are picked up automatically.

## Default JSON

When no `--format` / `?format=` is specified, the output is a JSON
array of `SolveResult` objects, one per requested platform.

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
        "sha256": "245c9ee8d688...",
        "md5": "c2a01a08fc99...",
        "size": 95931,
        "depends": ["__glibc >=2.17,<3.0.a0", "libzlib 1.3.2 h25fd6f3_2"],
        "constrains": []
      }
    ],
    "error": null
  }
]
```

Each entry has three fields:

`platform`
: The platform subdir this result was solved for.

`packages`
: A list of fully pinned packages with metadata (name, version, build,
  channel, URL, hashes, dependencies).

`error`
: `null` on success, or a string describing the solver failure for
  this platform. Partial failures are reported per-platform so one
  bad solve does not prevent results from other platforms.

## `explicit`

The conda `@EXPLICIT` format: one URL per line, with an `#md5` suffix.
This is the same format produced by `conda list --explicit`.

```bash
conda presto --format explicit -c conda-forge -p linux-64 zlib
```

```text
@EXPLICIT
https://conda.anaconda.org/conda-forge/linux-64/libgcc-14.2.0-h77fa898_1.conda#3cb76c3f10d3bc7f1571e53f0bd7be21
https://conda.anaconda.org/conda-forge/linux-64/libzlib-1.3.2-h25fd6f3_2.conda#e5db7304860e47218f312ddfab574c92
https://conda.anaconda.org/conda-forge/linux-64/zlib-1.3.2-h25fd6f3_2.conda#c2a01a08fc991620a74b32420e97868a
```

## `environment-yaml`

Aliases: `yaml`, `yml`, `env.yml`

A conda `environment.yml` file with pinned dependencies.

```bash
conda presto --format yaml -c conda-forge -p linux-64 zlib
```

```yaml
name: ""
channels:
  - conda-forge
dependencies:
  - libgcc=14.2.0=h77fa898_1
  - libzlib=1.3.2=h25fd6f3_2
  - zlib=1.3.2=h25fd6f3_2
```

## `environment-json`

Alias: `json`

A JSON representation of the environment spec.

```bash
conda presto --format json -c conda-forge -p linux-64 zlib
```

```json
{
  "name": "",
  "channels": ["conda-forge"],
  "dependencies": [
    "libgcc=14.2.0=h77fa898_1",
    "libzlib=1.3.2=h25fd6f3_2",
    "zlib=1.3.2=h25fd6f3_2"
  ]
}
```

## `requirements`

Aliases: `reqs`, `txt`

A pip-style `requirements.txt` with conda package pins. Useful as a
human-readable pinned list, though not directly installable with pip.

```bash
conda presto --format txt -c conda-forge -p linux-64 zlib
```

```text
libgcc==14.2.0
libzlib==1.3.2
zlib==1.3.2
```

## `conda-lock-v1`

A multi-platform `conda-lock.yml` file compatible with
[conda-lock](https://github.com/conda/conda-lock).

```bash
conda presto --format conda-lock-v1 -c conda-forge -p linux-64 zlib
```

```yaml
version: 1
metadata:
  channels:
    - url: conda-forge
  platforms:
    - linux-64
package:
  - name: zlib
    version: 1.3.2
    manager: conda
    platform: linux-64
    url: https://conda.anaconda.org/conda-forge/linux-64/zlib-1.3.2-h25fd6f3_2.conda
    hash:
      md5: c2a01a08fc991620a74b32420e97868a
      sha256: 245c9ee8d688...
```

## `rattler-lock-v6`

Alias: `pixi-lock-v6`

A `pixi.lock` file in rattler-lock v6 format, compatible with
[pixi](https://pixi.sh/) and `conda env create -f pixi.lock`.

```bash
conda presto --format pixi-lock-v6 -c conda-forge -p linux-64 zlib
```

```yaml
version: 6
environments:
  default:
    channels:
      - url: https://conda.anaconda.org/conda-forge/
    packages:
      linux-64:
        - conda: https://conda.anaconda.org/conda-forge/linux-64/zlib-1.3.2-h25fd6f3_2.conda
```

## Format discovery

Formats are registered through conda's exporter plugin system. Run
`conda presto --serve` and query `GET /formats` to see all available
formats in your installation, or pass an invalid `--format` name to
get the list in the error message.

## See also

- [CLI reference](cli.md)
- [HTTP API reference](http-api.md)
