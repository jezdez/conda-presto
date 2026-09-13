# Output formats

Without `--format` or `?format=`, conda-presto writes native JSON. Named formats come from conda's environment-exporter registry. Use `/formats` to see the installed names and aliases.

## Native JSON

The CLI and `/resolve` return one result per requested platform:

```json
[{"platform":"linux-64","packages":[],"error":null}]
```

| Field | Meaning |
|---|---|
| `platform` | Target conda subdirectory |
| `packages` | Selected package records |
| `error` | `null` on success, otherwise a safe error string and an empty package list |

Each package contains `name`, `version`, `build`, `build_number`, `channel`, `subdir`, `url`, `sha256`, `md5`, `size`, `depends`, `constrains` and `manager`. Values reflect the selected channel metadata. One platform may fail while others succeed.

## Exporters

| Primary name | Aliases | Media type | Multiple platforms in one document |
|---|---|---|:---:|
| `explicit` | None | `text/plain; charset=utf-8` | No |
| `environment-yaml` | `yaml`, `yml`, `env.yml` | `application/yaml` | No |
| `environment-json` | `json` | `application/json` | No |
| `requirements` | `reqs`, `txt` | `text/plain; charset=utf-8` | No |
| `conda-lock-v1` | `conda-lock` | `application/yaml` | Yes |
| `rattler-lock-v6` | `pixi`, `pixi-lock-v6` | `application/yaml` | Yes |
| `cyclonedx-json-v1.7` | `cyclonedx-json`, `cyclonedx`, `cdx-json` | `application/json` | No |

CycloneDX requires the optional conda-sboms provider. Other installed plugins can add formats.

## Format limitations

- `explicit` contains exact package URLs after `@EXPLICIT`. The current conda exporter does not append hashes to those URLs.
- Environment YAML and JSON contain selected dependencies. Solved environments have no target prefix name or configured channel list. Exact package URLs retain channel identity in formats that include them.
- `requirements` contains the original requested conda MatchSpecs. It is not a fully resolved lockfile or a pip requirements file.
- Conda-lock and rattler-lock record exact packages and hashes for all selected platforms in one document.
- Conda-sboms describes resolved conda records and their dependency relationships, not package payloads or a complete released product. Requested roots are preserved when available.

An exporter without a multiplatform callback is rendered separately for each platform and the texts are joined with a newline. Select one platform when `environment-json` or CycloneDX must be a valid JSON document. Use `/sbom` for separately packaged SBOM documents from a multiple-platform request.

## Failures

Named exporter output requires every platform solve to succeed. A solve or rendering failure exits the CLI with status 1 or returns HTTP 500. Unknown names return an error with available registry names. Native JSON remains the format for per-platform solve errors.

See {doc}`http-api`, {doc}`cli` and {doc}`../how-to/transcode-lockfiles` for invocation and conversion details.
