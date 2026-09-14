# HTTP API

Start the service with `conda presto --serve`. `/openapi.json` and `/` return the generated OpenAPI document. The manually dispatched bodies of `/resolve` and `/transcode` are described below.

| Method | Path | Operation |
|---|---|---|
| GET, POST | `/resolve` | Solve requirements for selected platforms |
| POST | `/parse` | Read requirements or discover and select workspace environments |
| POST | `/transcode` | Convert supported lockfiles without solving or downloading packages |
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
| `platforms` | Array of platform names | Server host platform |
| `file` | Environment file content as a string | Absent |
| `filename` | Parser hint such as `environment.yml` | Inferred |

Provide specs or file content. File requirements are combined with `specs`. Explicit channels override file channels. Body fields override the equivalent query parameters, including explicit empty arrays.

Both resolve methods accept repeated `spec`, `channel` and `platform` query parameters. `format` selects an installed exporter and is query-only. Without it, the response is a native JSON array with an `error` field per platform. Exporter output requires every platform to succeed. See {doc}`output-formats`.

Raw files can be uploaded with `application/yaml`, `application/toml`, `text/plain`, or their `text/*` and `application/x-*` YAML/TOML equivalents. An installed parser must recognize the file. Use `?filename=pixi.lock` when a parser hint is needed. HTTP inputs reject `@EXPLICIT` files, YAML aliases and structures exceeding 10,000 nodes.

Files named `*.txt`, including the default for `text/plain` uploads, use conda's requirements parser. Put one MatchSpec per line. Blank lines and `#` comments are allowed. Upload YAML with a `.yml` or `.yaml` filename.

```bash
curl --fail-with-body --data-binary @environment.yml \
  -H 'Content-Type: application/yaml' \
  'http://localhost:8000/resolve?platform=linux-64&format=pixi-lock-v6' \
  -o pixi.lock
```

HTTP lockfile parsing reads format and platform metadata. `/resolve` rejects requests that would need it to load package records. Use `/transcode` for supported conversion.

Workspace manifests must use `/parse`. `/resolve` and `/sbom` reject them until workspace solving is supported.

## Parse

`POST /parse` accepts a JSON body with the following fields:

| Field | Type | Default |
|---|---|---|
| `file` | File content as a string | Required |
| `filename` | Parser hint such as `environment.yml` or `conda.toml` | `environment.yml` |
| `environments` | Array of workspace environment names | Absent |
| `platforms` | Array of workspace platform names or conda subdirectories | Absent |

Ordinary environment files return `{"specs":[...],"channels":[...]}` without solving. Lockfile metadata parsing does not materialize package records, so it does not derive specs or channels from them. Workspace selectors are rejected for ordinary environment and lock files.

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

(http-transcode)=
## Transcode

`POST /transcode?format=conda-lock-v1` accepts raw file uploads as above, or JSON with `file`, `filename` and `platforms`. The default platform is the server host. Query parameters are `format`, repeated `platform`, and `filename`.

The supported formats are `conda-lock-v1` and `rattler-lock-v6`, including their registered aliases. Conversion uses metadata already in the file. It neither solves nor downloads package archives. Nonempty `specs` or `channels` are rejected because applying them would require a solve.

Conversion also rejects data it cannot preserve safely:

- Conda-lock v1 pip, optional or non-main packages on a selected platform.
- Rattler v6 multiple environments, PyPI references and fields the compatibility model cannot represent.
- Cross-format conda-pypi wheel mappings, which would lose package identity.
- Invalid or duplicate records, dangling references, URL/package identity mismatches and wrong-platform URLs.
- Constraints, features, Python site-package paths, duplicate dependency names and dependency selectors when targeting conda-lock v1.

These restrictions can apply even when source and target formats match. Representable informational metadata may be normalized by the exporter. Rejections return HTTP 400 with a `reasons` array. Successful conversion uses `Cache-Control: no-store`. See the {doc}`conversion example <../how-to/transcode-lockfiles>`.

## Retained outputs

Successful eligible solves return a relative `Location: /r/{key}`. The key identifies the saved bytes and media type. It is not the artifact's bare SHA256 digest. `GET /r/{key}` returns that exact output without checking current channel metadata.

A new `/resolve` request checks freshness before reusing a solve. Missing or evicted outputs return HTTP 404. An absent `Location` means the response was not retained. This can happen with credential-bearing requests or outputs, storage limits, or failed publication to shared storage. The cache is not an archive. See {doc}`cache`.

## Optional SBOM generation

`POST /sbom` accepts the resolve JSON fields, with at least one explicit platform. It solves new requirements and delegates to conda-sboms' `cyclonedx-json-v1.7` exporter. Supplied resolved lockfiles are rejected. It does not inspect installed files or scan for vulnerabilities.

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

`GET /capabilities` returns booleans named `sbom`, `sign`, `verify`, `workspace_parse` and `workspace_solve`. `workspace_parse` reports workspace discovery and selection support. `workspace_solve` remains false. `sign` reports installed support and enabled configuration, not a guarantee that credentials or remote signing services are currently available. Provider versions, including conda-workspaces, appear in `/version` when installed.

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
