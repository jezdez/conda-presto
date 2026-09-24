# Output formats

Without `--format` or `?format=`, solves return native JSON. Export operations require a named format from conda's environment-exporter registry. Use `/formats` to see installed names and aliases. The source data and selected targets determine which formats can represent an export.

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

Workspace solves return one row per selected environment and target:

```json
[{"environment":"test","platform":"linux-64","subdir":"linux-64","packages":[],"error":null}]
```

`platform` is the logical workspace target name and `subdir` is its concrete conda platform. They can differ for named variants. `environment` distinguishes solutions for different environments on the same target.

## Exporters

| Primary name | Aliases | Media type | Multiple platforms in one document |
|---|---|---|:---:|
| `explicit` | None | `text/plain; charset=utf-8` | No |
| `environment-yaml` | `yaml`, `yml`, `env.yml` | `application/yaml` | No |
| `environment-json` | `json` | `application/json` | No |
| `requirements` | `reqs`, `txt` | `text/plain; charset=utf-8` | No |
| `conda-lock-v1` | `conda-lock` | `application/yaml` | Yes |
| `rattler-lock-v6` | `pixi`, `pixi-lock-v6` | `application/yaml` | Yes |
| `conda-workspaces-lock-v1` | `conda-workspaces-lock`, `workspace-lock` | `application/yaml` | Yes |
| `conda-toml` | None | `application/toml` | Yes, one environment |
| `pixi-toml` | None | `application/toml` | Yes, one environment |
| `pyproject-toml` | None | `application/toml` | Yes, one environment |
| `cyclonedx-json-v1.7` | `cyclonedx-json`, `cyclonedx`, `cdx-json` | `application/json` | No |

CycloneDX requires the optional conda-sboms provider. Other installed plugins can add formats.

The workspace lock and TOML formats come from the required conda-workspaces provider.

## Format limitations

- `explicit` contains exact package URLs after `@EXPLICIT`. The current conda exporter does not append hashes to those URLs.
- Environment YAML and JSON contain selected dependencies. Ordinary solved environments have no target prefix name or configured channel list. Workspace solves retain environment names and channels. Exact package URLs retain channel identity in formats that include them.
- `requirements` contains the original requested conda MatchSpecs. It is not a fully resolved lockfile or a pip requirements file.
- Conda-lock and rattler-lock record exact packages and hashes for all selected platforms in one document.
- `conda-workspaces-lock-v1` combines named environments and logical targets into one `conda.lock`, preserving full package records supported by the provider.
- TOML exporters produce normalized requested dependencies, channels and concrete platforms for one selected environment. They do not preserve comments, tasks, feature organization, activation settings, channel priority or system requirements. Select targets with distinct concrete subdirectories and identical ordered channels. These exports are not fully pinned lockfiles.
- Conda-sboms describes resolved conda records and their dependency relationships, not package payloads or a complete released product. Requested roots are preserved when available.

On ordinary solve paths, an exporter without a multiplatform callback is rendered separately for each platform and the texts are joined with a newline. Select one platform when `environment-json` or CycloneDX must be a valid JSON document. No-solve export requires one target for these exporters. `/sbom` returns separate complete documents for solved requirements or explicitly selected workspace lock environments and targets.

## Failures

Named exporter output requires every platform solve to succeed. A solve or rendering failure exits the CLI with status 1 or returns HTTP 500. Unknown names return an error with available registry names. Native JSON remains the format for per-platform solve errors.

Workspace failures identify the affected environment and target. Combined locks require every selected pair to succeed. Unsupported manifest-export selections are rejected before rendering.

No-solve export rejects unsupported inputs, output formats or selections with HTTP 400 or CLI status 1. It does not fall back to a solve.

See {doc}`http-api`, {doc}`cli` and {doc}`../how-to/transcode-lockfiles` for invocation and conversion details.

## Exporting declarations

`POST /export` and CLI `--export` render workspace manifests, environment YAML and requirements files without solving. Conda's specifier registry reads ordinary inputs, which reject platform and environment selectors during export. Conda-workspaces' `WorkspaceContext.envs_from_manifest()` builds selected workspace environments for registered exporters.

Use normalized TOML, environment YAML, environment JSON or requirements output for unsolved declarations. Locks and explicit output require exact package records, and conda-sboms also rejects input without those records. Use `/resolve?format=FORMAT` or a CLI solve for these outputs.

Workspace declaration exports require one selected environment. TOML can represent multiple targets with distinct concrete subdirs and identical ordered channels. Single-target exporters require one target. Normalization does not preserve the original source comments, tasks, feature composition or every workspace setting. See {doc}`../how-to/parse-workspace` for a runnable example.

## Exporting saved workspace locks

`POST /export` and CLI `--export` render selected exact records from `conda.lock` without solving. Workspace lock output uses conda-workspaces' source selection API, preserving saved URLs, hashes and metadata. Other outputs use registered conda exporters. Normalized TOML and environment YAML are different representations and do not preserve all lock metadata or the original workspace manifest.

Select one environment for non-workspace outputs and one target for exporters without a multiplatform callback. Logical targets sharing a concrete subdir cannot be combined in those outputs. Targets with an unknown concrete subdir require a matching companion manifest for other exports. Workspace lock extraction preserves them without that context. Conversions from workspace locks to `conda-lock-v1` and `rattler-lock-v6` fail explicitly until their provider can preserve the saved metadata. See {doc}`../how-to/extract-workspace-lock` for examples and the upstream provider requirements.

For CycloneDX, lock-only export preserves conda-sboms' inferred graph roots. A validated companion manifest supplied through HTTP `manifest` and `manifest_filename` or CLI `--manifest` provides declared requested roots through conda-workspaces. The provider records which root source it used and retains its coverage markers. HTTP `/sbom` applies this same export path once per selected environment and target, returning a collection of complete documents without solving.
