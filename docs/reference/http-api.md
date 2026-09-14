# HTTP API

Start the service with `conda presto --serve`. `/openapi.json` and `/` return the generated OpenAPI document. The manually dispatched bodies of `/resolve`, `/transcode` and `/export` are described below.

| Method | Path | Operation |
|---|---|---|
| POST | `/parse` | Read requirements or discover and select workspace environments |
| GET, POST | `/resolve` | Solve requirements or selected workspace environments, optionally rendering an exporter format |
| POST | `/export` | Render declarations or selected locked records without solving |
| POST | `/transcode` | Convert supported lockfiles through the lock-to-lock compatibility operation |
| POST | `/sbom` | Export separate SBOMs from solved requirements or selected workspace lock entries |
| POST | `/validate` | Check a complete workspace lock against its manifest without solving |
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

Provide specs or file content. Ordinary file requirements are combined with `specs`, and explicit channels override their file channels. Workspace requests use the manifest's requirements and channels and reject inline specs and channel overrides. Body fields override equivalent query parameters by presence.

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

Omitting both selectors returns discovery with `selected: []`. Supplying either selects the requested saved entries, defaulting the other selector to all available values. A concrete subdir is accepted only if it identifies one target in each selected environment. For a logical target containing only noarch packages or no packages, `subdir` is `null` when the backing platform cannot be inferred. Such a target can be extracted as a workspace lock. Exporters requiring a concrete subdir need a matching companion manifest to supply it.

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

Select one environment for outputs other than a workspace lock. Targets must resolve to distinct concrete subdirs from the saved records or a matching companion manifest. Exporters without a multiplatform callback require one target, so the response is one valid document. TOML exports also require the same ordered channels across selected targets. A registered exporter name does not imply that every input or target selection can produce that format.

For workspace `conda.lock`, `format=conda-workspaces-lock-v1` and its aliases preserve selected source entries, including URLs, hashes and metadata. Other supported outputs use conda's exporter registry with exact saved records. Normalized outputs do not preserve every lock field or recover the original manifest. Conversion from workspace locks to `conda-lock-v1` and `rattler-lock-v6` is rejected because the current provider discards some saved metadata.

JSON workspace-lock exports can include `manifest` with a companion workspace manifest's content and `manifest_filename` set to `conda.toml`, `pixi.toml` or `pyproject.toml`. The selected environment and target, concrete platform, ordered channels and direct requirements must agree with the saved records. A matching manifest can supply the concrete subdir for a logical target whose saved records are all noarch. It also supplies declared roots to exporters such as conda-sboms. Without a companion manifest, SBOM roots are inferred from the saved dependency graph. PyPI declarations are unsupported in companion manifests. Source workspace-lock extraction rejects manifest context because it only copies saved source entries.

Export runs inside the bounded parser process. It does not fetch repodata, download archives, install packages or execute tasks. Successful eligible workspace lock exports return a retained location. Declaration exports and ordinary lock conversions use `Cache-Control: no-store`. The workspace lock request identity includes uploaded content, environment and target selections, output format and provider versions. See {doc}`../how-to/extract-workspace-lock` for saved-record examples and {doc}`../how-to/resolve-from-cli` for declaration export.

(http-transcode)=
## Transcode compatibility

`POST /transcode?format=FORMAT` keeps the lock-to-lock compatibility operation. It accepts the same file envelopes and selectors as `/export`, but requires both input and output to be lock formats. It does not solve or download packages. Workspace source extraction is available on this route too, with the same selection restrictions as `/export`.

The compatibility route rejects `manifest` and `manifest_filename`. Use `/export` when a supported exporter needs companion manifest context.

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

`POST /sbom` accepts JSON and delegates rendering to conda-sboms' `cyclonedx-json-v1.7` exporter. Ordinary resolve fields retain their solve-based behavior, with at least one explicit platform:

```json
{"sboms":[{"platform":"linux-64","content":"...exact CycloneDX JSON text...","sha256":"...","location":"/r/..."}]}
```

To use saved records without solving, supply `file` with the lock content, `filename: "conda.lock"`, and explicit nonempty `environments` and `platforms` arrays. Logical target names are preserved, including multiple targets backed by the same conda subdir. Extra specs and channel overrides are rejected. Each selected pair adds an item with this shape:

```json
{"environment":"test","platform":"linux-cuda","subdir":"linux-64","content":"...exact CycloneDX JSON text...","sha256":"...","location":"/r/..."}
```

Optional `manifest` and `manifest_filename` fields have the same meaning and validation as `/export`. A companion manifest supplies direct requested roots only after its selected requirements are checked against the saved records. Without it, conda-sboms infers roots from the saved graph and reports `inferred-graph-roots` in its environment properties. Matching manifest roots are reported as `requested-packages`. This checks the supplied context, not whether a lock is current with every manifest setting.

Each `content` is a separate complete document. Package URLs, hashes, dependency edges and the provider's coverage markers come from the selected records. `location` is present only if that document was retained. Lock rendering runs in the bounded parser process without repodata access, downloads or solves. Any invalid selection or failed rendering rejects the request without returning a successful partial collection. Direct workspace-manifest SBOM requests and ordinary conda-lock or rattler-lock uploads remain unsupported.

SBOMs describe resolved conda package records. They do not inspect installed files, scan for vulnerabilities or establish the complete contents of a released product. See {doc}`../how-to/extract-workspace-lock` for HTTP and CLI examples.

Save the UTF-8 bytes of `content` unchanged. For example, `jq -j '.sboms[0].content' response.json > environment.cdx.json` avoids adding a newline.

## Check workspace lock consistency

`POST /validate` accepts a JSON envelope containing both complete uploaded files:

| Field | Type | Meaning |
|---|---|---|
| `file` | String | Workspace `conda.lock` content |
| `filename` | String | Required parser hint `conda.lock` |
| `manifest` | String | Workspace manifest content |
| `manifest_filename` | String | Required parser hint `conda.toml`, `pixi.toml` or `pyproject.toml` |

All four fields are required. This checks the whole workspace, so the endpoint rejects additional fields, including `specs`, `channels`, `platforms`, `environments` and `format`. It also rejects query parameters. Ordinary lockfile formats, PyPI dependencies, external package references and `archspec` system requirements are unsupported. The upstream checker currently treats `archspec` as a version constraint, while conda represents it as a virtual package build.

The operation reuses conda-workspaces' `check_lockfile_satisfiability()` to compare the manifest with saved records for every declared environment and logical target. Checks include required packages, ordered channels, dependency edges, constraints and virtual package requirements. Referenced package records are checked. Unused top-level metadata follows the provider's handling and is not independently validated. Logical targets remain distinct even when they use the same concrete conda subdirectory. Results do not depend on the server host platform.

Both a consistent lock and a supported mismatch return HTTP 200:

```json
{
  "consistent": false,
  "targets": [
    {
      "environment": "default",
      "platform": "linux-64",
      "subdir": "linux-64",
      "consistent": false,
      "reason": "A provider diagnostic describing the mismatch"
    }
  ]
}
```

`consistent` is true only when every target passes. Each target includes its logical `platform`, concrete `subdir` and a provider `reason` for a mismatch. A consistent target has `reason: null`. Workspace-wide declaration mismatches can repeat across target results and identify another affected environment. Treat reasons as diagnostic text, not stable error codes. Malformed or unsupported input returns HTTP 400 without a partial result. The shared parser timeout produces HTTP 504.

Checking runs in the bounded parser process without solving, fetching repodata, downloading archives, installing packages or executing tasks. Check results use `Cache-Control: no-store` and are not retained. Consistency does not establish package freshness, archive integrity, vulnerability policy or whether the lock is the newest possible solution. See {doc}`../tutorials/http-api` for an example with a changed manifest.

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

`GET /capabilities` returns booleans named `export`, `sbom`, `sign`, `verify`, `workspace_parse`, `workspace_solve`, `workspace_lock_parse`, `workspace_lock_export`, `workspace_lock_sbom` and `workspace_lock_check`. `export` reports the no-solve export operation, subject to the input and format restrictions above. The lock capabilities report workspace lock inspection, selection and exact-record export support. `workspace_lock_sbom` requires the installed CycloneDX exporter and reports support for named locked-environment collections. `workspace_lock_check` reports whole-workspace manifest–lock consistency checking. `workspace_parse` and `workspace_solve` report workspace discovery, selection and solving support. `sign` reports installed support and enabled configuration, not a guarantee that credentials or remote signing services are currently available. Provider versions, including conda-workspaces, appear in `/version` when installed.

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
