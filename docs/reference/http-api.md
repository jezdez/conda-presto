# HTTP API

Start the service with `conda presto --serve`. `/openapi.json` and `/` return the generated OpenAPI document. The manually dispatched bodies of `/resolve`, `/transcode` and `/export` are described below.

| Method | Path | Operation |
|---|---|---|
| POST | `/parse` | Read requirements or discover and select workspace environments |
| GET, POST | `/resolve` | Solve requirements or selected workspace environments, optionally rendering an exporter format |
| POST | `/export` | Render declarations or selected locked records without solving |
| POST | `/transcode` | Convert supported lockfiles through the lock-to-lock compatibility operation |
| POST | `/sbom` | Solve requirements and export separate platform SBOMs |
| POST | `/sign` | Sign a retained output using the service identity |
| POST | `/verify` | Check supplied bytes, a bundle and an expected signer |
| GET | `/r/{key}` | Retrieve an exact retained output |
| GET | `/formats` | Installed exporter names and aliases |
| GET | `/capabilities` | Availability of optional SBOM and Sigstore adapters |
| GET | `/platforms` | Known conda platform subdirectories |
| GET | `/version` | Installed component versions |
| GET | `/health` | Worker readiness |

## Resolve

`POST /resolve` accepts this JSON body:

```json
{"specs":["python=3.13","numpy"],"channels":["conda-forge"],"platforms":["linux-64"]}
```

| Field | Type | Default |
|---|---|---|
| `specs` | Array of MatchSpec strings | Empty |
| `channels` | Array of channel names or URLs | Server defaults |
| `platforms` | Array of conda subdirectories or workspace target names | Host for ordinary inputs, declared targets for workspaces |
| `environments` | Array of workspace environment names | All workspace environments |
| `file` | Environment file content as a string | Absent |
| `filename` | Parser hint such as `environment.yml` | Inferred |

Provide specs or file content. Ordinary file requirements are combined with `specs`, and explicit channels override their file channels. Workspace requests use the manifest's requirements and channels and reject inline specs and channel overrides. The server checks dependency-specific channels against its channel allowlist as well. Body fields override equivalent query parameters by presence.

Both resolve methods accept repeated `spec`, `channel` and `platform` query parameters. `format` selects an installed exporter and is query-only. Without it, the response is a native JSON array with an `error` field per platform. Exporter output requires every platform to succeed. See {doc}`output-formats`.

`POST /resolve` also accepts repeated `environment` query parameters for workspace input. JSON uses the plural `environments` field. Workspace selectors are rejected for ordinary inputs.

Raw files can be uploaded with `application/yaml`, `application/toml`, `text/plain`, or their `text/*` and `application/x-*` YAML/TOML equivalents. An installed parser must recognize the file. Use `?filename=pixi.lock` when a parser hint is needed. HTTP inputs reject `@EXPLICIT` files, YAML aliases and structures exceeding 10,000 nodes.

Files named `*.txt`, including the default for `text/plain` uploads, use conda's requirements parser. Put one MatchSpec per line. Blank lines and `#` comments are allowed. Upload YAML with a `.yml` or `.yaml` filename.

```bash
curl --fail-with-body --data-binary @environment.yml \
  -H 'Content-Type: application/yaml' \
  'http://localhost:8000/resolve?platform=linux-64&format=pixi-lock-v6' \
  -o pixi.lock
```

HTTP lockfile parsing reads embedded metadata without downloading packages. `/resolve` rejects workspace locks, including requests that add specs or channel overrides. Use `/export` to extract or render saved records. Ordinary lockfile requests that need package records are also rejected by `/resolve`.

For workspace manifests, omitted environments select all environments and omitted platforms select each environment's declared targets. Omitting both solves the whole declared selection. This differs from `/parse`, where omitting both selectors performs discovery. An environment without declared platforms requires an explicit platform selection. Empty selectors, unknown names and ambiguous platform selections are rejected.

Use `?format=conda-workspaces-lock-v1` for one combined `conda.lock` containing every selected environment and target. Any failed pair prevents a successful incomplete lock response. Native JSON returns one entry per pair, adding `environment` and `subdir`, with `platform` preserving the logical target name.

The `conda-toml`, `pixi-toml` and `pyproject-toml` formats produce normalized dependency declarations for one selected environment. Selected targets must have distinct concrete subdirectories and the same ordered channels. See {doc}`output-formats` for what these exports preserve.

Workspace solves use the selection limits and dependency restrictions described under Parse. Successful eligible outputs use the existing `/r/` retention mechanism. See {doc}`../how-to/parse-workspace` for a complete workflow.

## Parse

`POST /parse` accepts a JSON body with the following fields:

| Field | Type | Default |
|---|---|---|
| `file` | File content as a string | Required |
| `filename` | Parser hint such as `environment.yml` or `conda.toml` | `environment.yml` |
| `environments` | Array of workspace environment names | Absent |
| `platforms` | Array of workspace platform names or conda subdirectories | Absent |

Ordinary environment files return `{"specs":[...],"channels":[...]}` without solving. Ordinary lockfile metadata parsing does not derive specs or channels from package records. Workspace selectors are accepted for workspace manifests and `conda.lock` files.

Workspace manifests use conda-workspaces, included in standard installations. Supported filenames are `conda.toml`, `pixi.toml` and `pyproject.toml`, subject to the provider's supported workspace syntax. A workspace response has three fields:

| Field | Content |
|---|---|
| `format` | Canonical manifest exporter name: `conda-toml`, `pixi-toml` or `pyproject-toml` |
| `environments` | Environment declarations with `name`, `features`, `no_default_feature` and a `platforms` mapping from logical names to conda subdirectories |
| `selected` | Composed declarations for each selected environment and platform |

Each `selected` entry contains `environment`, `platform`, `subdir`, `specs`, `channels`, `channel_priority`, `system_requirements` and `pypi_dependencies`. `platform` preserves the logical workspace target name. `subdir` identifies its underlying conda platform. These are declared requirements, not solved package records.

Omit both selectors to discover environments and their declared platforms. Discovery returns `selected: []`. Supplying either selector requests composed declarations. If `environments` is omitted, all environments are selected. If `platforms` is omitted, each selected environment uses its declared platforms. An environment without declared platforms requires an explicit platform selection.

Empty selectors, unknown names and ambiguous platform selections are rejected. Selected local, Git or URL PyPI sources are unsupported. Version requirements that need unavailable optional conda-pypi support produce an error instead of being omitted.

The number of selected environment/platform combinations is limited by `CONDA_PRESTO_MAX_PLATFORMS`. Existing specs and channels limits apply to each target. See {doc}`../how-to/parse-workspace` for discovery and selection examples.

### Workspace locks

With `filename: "conda.lock"`, parsing uses conda-workspaces' lock loader. The response has `format: "conda-workspaces-lock-v1"`, `environments` entries containing `name` and `platforms`, and `selected` entries containing `environment`, `platform` and `subdir`. Saved logical target names remain distinct from concrete conda subdirectories.

Omitting both selectors returns discovery with `selected: []`. Supplying either selects the requested saved entries, defaulting the other selector to all available values. A concrete subdir is accepted only if it identifies one target in each selected environment. For a logical target containing only noarch packages or no packages, `subdir` is `null` when the backing platform cannot be inferred. Such a target can be extracted as a workspace lock but cannot be exported to a format requiring a concrete subdir.

Inspection uses embedded metadata only. Malformed selected records, unsupported external references, missing names, inconsistent hashes or package identities and credential-bearing input fail explicitly. Lock requests use the configured target and channel count limits. They do not apply the solve channel allowlist because they never fetch channel data.

## Export

`POST /export?format=FORMAT` renders the supplied file without solving. It accepts raw uploads as above, or JSON with `file`, `filename`, `platforms` and `environments`. Query parameters are `format`, repeated `platform`, repeated `environment`, and `filename`. Both file content and `format` are required. Additional specs and channel overrides are rejected.

| Input | Supported export |
|---|---|
| Workspace manifest | Compose selected declarations with conda-workspaces and render a normalized dependency format |
| Environment YAML or requirements file | Parse declared dependencies through conda and render a normalized dependency format |
| Workspace `conda.lock` | Extract selected source entries, or render supported formats from exact saved records |
| Ordinary conda-lock or rattler-lock | Use the existing supported no-download lock-to-lock conversions |

Declarations can produce normalized `conda-toml`, `pixi-toml`, `pyproject-toml`, environment YAML, environment JSON or requirements output. They cannot produce locks, explicit package URLs or SBOMs without resolved package records. Use `/resolve?format=FORMAT` when the output requires a solve. Normalized exports preserve supported dependency declarations rather than the original comments, tasks, feature composition or source metadata.

Workspace selectors use declared targets for manifests and saved targets for locks. Omitted selectors include all environments and targets. Ordinary declaration exports reject platform and environment selectors, leaving the parser's environment unchanged. Ordinary lockfiles use the server host platform unless `platform` is supplied. Empty, unknown and ambiguous workspace selectors fail.

Select one environment for outputs other than a workspace lock. Targets must have distinct known concrete subdirs. Exporters without a multiplatform callback require one target, so the response is one valid document. TOML exports also require the same ordered channels across selected targets. A registered exporter name does not imply that every input or target selection can produce that format.

For workspace `conda.lock`, `format=conda-workspaces-lock-v1` and its aliases preserve selected source entries, including URLs, hashes and metadata. Other supported outputs use conda's exporter registry with exact saved records. Normalized outputs do not preserve every lock field or recover the original manifest. Conversion from workspace locks to `conda-lock-v1` and `rattler-lock-v6` is rejected because the current provider discards some saved metadata.

Export runs inside the bounded parser process. It does not fetch repodata, download archives, install packages or execute tasks. Successful eligible workspace lock exports return a retained location. Declaration exports and ordinary lock conversions use `Cache-Control: no-store`. The workspace lock request identity includes uploaded content, environment and target selections, output format and provider versions. See {doc}`../how-to/extract-workspace-lock` for saved-record examples and {doc}`../how-to/resolve-from-cli` for declaration export.

(http-transcode)=
## Transcode compatibility

`POST /transcode?format=FORMAT` keeps the lock-to-lock compatibility operation. It accepts the same file envelopes and selectors as `/export`, but requires both input and output to be lock formats. It does not solve or download packages. Workspace source extraction is available on this route too, with the same selection restrictions as `/export`.

Ordinary lock conversion through either route supports `conda-lock-v1` and `rattler-lock-v6`, including their registered aliases. Workspace locks default to all saved environments and targets. Ordinary lockfiles default to the server host platform.

Conversion also rejects data it cannot preserve safely:

- Conda-lock v1 pip, optional or non-main packages on a selected platform.
- Rattler v6 multiple environments, PyPI references and fields the compatibility model cannot represent.
- Cross-format conda-pypi wheel mappings, which would lose package identity.
- Invalid or duplicate records, dangling references, URL/package identity mismatches and wrong-platform URLs.
- Constraints, features, Python site-package paths, duplicate dependency names and dependency selectors when targeting conda-lock v1.

These ordinary-lock restrictions can apply even when source and target formats match. Representable informational metadata may be normalized by the exporter. Rejections return HTTP 400, with a `reasons` array for operation-level rejections. Ordinary lock conversion uses `Cache-Control: no-store`. See the {doc}`conversion example <../how-to/transcode-lockfiles>`.

## Retained outputs

Successful eligible solves and workspace lock exports return a relative `Location: /r/{key}`. The key identifies the saved bytes and media type. It is not the artifact's bare SHA256 digest. `GET /r/{key}` returns that exact output without checking current channel metadata. Ordinary lock conversions continue to use `Cache-Control: no-store`.

A new `/resolve` request checks freshness before reusing a solve. Missing or evicted outputs return HTTP 404. An absent `Location` means the response was not retained. This can happen with credential-bearing requests or outputs, storage limits, or failed publication to shared storage. The cache is not an archive. See {doc}`cache`.

## Optional SBOM generation

`POST /sbom` accepts the ordinary resolve JSON fields, with at least one explicit platform. It solves new requirements and delegates to conda-sboms' `cyclonedx-json-v1.7` exporter. Workspace manifests and supplied resolved lockfiles are rejected. It does not inspect installed files or scan for vulnerabilities.

SBOM rendering uses the same exporter registry as other output formats. `/sbom` remains a solve-based wrapper that collects separate platform documents. Named locked-environment SBOM collections are tracked in [#128](https://github.com/jezdez/conda-presto/issues/128).

```json
{"sboms":[{"platform":"linux-64","content":"...exact CycloneDX JSON text...","sha256":"...","location":"/r/..."}]}
```

Each `content` is a separate complete document. Requested roots and exact selected package records are passed to the provider, whose coverage markers remain intact. `location` is present only if that document was retained. Any failed platform makes the request fail without returning a successful SBOM collection.

Save the UTF-8 bytes of `content` unchanged. For example, `jq -j '.sboms[0].content' response.json > environment.cdx.json` avoids adding a newline.

## Optional signing

`POST /sign` accepts only `{"key":"..."}`, using the key from a retained output's `/r/` location. It does not accept caller-authored artifacts or statements.

```json
{"artifact_name":"result-...","sha256":"...","bundle":"...Sigstore bundle JSON text..."}
```

The bundle binds the exact saved bytes to an authenticated signer. Its standard [in-toto Link v0.3](https://github.com/in-toto/attestation/blob/main/spec/predicates/link.md) describes an output-signing step named `conda-presto-sign`, with the same digest as material and output. It records a later signing operation, not the original solve or its consumed inputs.

Signing is disabled by default. Operators must enable it and choose a trust configuration or deliberately allow public Sigstore. Unavailable credentials cause an error without interactive login. Save the artifact and bundle together. Detailed solve construction evidence remains {doc}`deferred <../proposals>`.

## Optional verification

`POST /verify` accepts:

| Field | Value |
|---|---|
| `artifact` | Base64 of the exact artifact bytes |
| `bundle` | Sigstore bundle JSON as a string |
| `artifact_name` | Expected statement subject name, returned by `/sign` |
| `expected_identity` | Recipient-approved signer identity |
| `expected_issuer` | Recipient-approved identity issuer |

The service uses conda-sigstore to verify the bundle, requires exactly one subject with a SHA256 digest, hashes the supplied bytes and compares both identity and issuer. The recipient must choose that pair independently of the supplied bundle.

Success returns `signature_verified`, `artifact_verified` and `signer_verified` as `true`, plus `artifact_name`, `artifact_sha256`, `identity`, `issuer` and `predicate_type`. `claims_checked` is true only for the recognized signing-step shape and matching material/output. Other predicates have `claims_checked: false`. This does not prove the truth of arbitrary construction claims or product compliance.

## Availability, limits and errors

`GET /capabilities` returns booleans named `export`, `sbom`, `sign`, `verify`, `workspace_parse`, `workspace_solve`, `workspace_lock_parse` and `workspace_lock_export`. `export` reports the no-solve export operation, subject to the input and format restrictions above. The lock capabilities report workspace lock inspection, selection and exact-record export support. `workspace_parse` and `workspace_solve` report workspace discovery, selection and solving support. `sign` reports installed support and enabled configuration, not a guarantee that credentials or remote signing services are currently available. Provider versions, including conda-workspaces, appear in `/version` when installed.

`GET /health` returns HTTP 200 with `{"status":"ok"}` when the configured persistent worker is ready. A stopped worker produces HTTP 503 with `{"status":"unavailable"}` while recovery begins. Without persistent-worker mode, the probe reports HTTP 200.

| Status | Meaning |
|---|---|
| 400 | Invalid request, disallowed channel, unsupported format or conversion |
| 404 | Output not retained |
| 413 | Body exceeds the configured limit |
| 422 | Supplied attestation, artifact or signer check failed |
| 429 | Client rate limit exceeded |
| 500 | Unexpected solve or exporter failure |
| 503 | Provider, signing credentials, trust material or worker unavailable |
| 504 | Parsing, solving or attestation operation timed out |

Handler errors have an `error` field. Attestation errors can also carry a machine-readable `code`. Framework validation and middleware use Litestar's error shape.

All bodies use the configured request-size limit, including base64 verification input. Attestation operations also cap artifacts at 32 MiB and bundles at 10 MiB. Signing and verification share the request capacity limit and run in terminable processes with the solve timeout. Conda cache-state inspection cannot safely abandon its process-global context, so that work can extend observed solve duration. Settings are listed in {doc}`environment-variables`.
