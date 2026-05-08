# cep-solve-attestation: CEP draft — solve attestations for the conda ecosystem

Status: draft text for upstream submission to conda/ceps
Owner: TBD (proposed co-authors: Wolf Vollprecht / prefix.dev,
William Woodruff / Trail of Bits, plus this project's maintainers)
Filed: 2026-04-16
Depends on: nothing for the CEP itself; in conda-presto, this is
the schema [attestation](attestation.md) implements.
Companion plans: [receipt](receipt.md) (local receipts), [attestation](attestation.md) (sigstore
attestations), [serving-attestations](serving-attestations.md) (`.sigs` sidecar serving convention,
mirroring [conda/ceps#142](https://github.com/conda/ceps/pull/142)),
[admit](admit.md) (admission engine that consumes attestations of this
predicate type).

## What this file is

This is a **proposal document** for the conda governance process,
not a feature plan for conda-presto itself. The body below is the
draft text, written in the same shape as
[CEP-27](https://github.com/conda/ceps/blob/main/cep-0027.md), ready
to be polished and submitted as a pull request to
[conda/ceps](https://github.com/conda/ceps).

Filing this as a CEP serves three purposes:

1. **Standardize the predicate type** so multiple implementations
   (conda-presto, future tools, third-party verifiers) all agree on
   the schema for "I solved this environment from these inputs."
2. **Build on CEP-27's accepted infrastructure** — same in-toto +
   DSSE + Sigstore stack, just a new predicate type. Low marginal
   ask of the conda governance process.
3. **Position conda-presto as the reference implementation** of an
   accepted standard, the same way `rattler-build` is the reference
   for CEP-27.

The CEP and the conda-presto implementation should ideally land in
the same calendar window: CEP draft submitted for review, code in a
branch demonstrating the predicate works end-to-end. CEP-27 followed
this pattern with `rattler-build` and `prefix-dev/sigstore-example`.

## Status of upstream coordination

- [ ] Confirm interest from CEP-27 authors (Wolf Vollprecht @
      prefix.dev, William Woodruff @ Trail of Bits) in
      co-authoring or supporting
- [ ] Open a discussion thread on conda/ceps before opening the PR
      (the CEP-27 PR is conda/ceps#112; the discussion-first
      approach is the established norm)
- [ ] Mention in the conda Plugins SIG meeting
- [ ] Tag relevant downstream implementers (rattler, mamba, pixi)
      so they're aware

---

# (Draft begins below — would become `cep-NNNN.md` upstream)

# CEP NNNN - Standardizing a solve attestation for the conda ecosystem

<table>
<tr><td> Title </td><td> Standardizing a solve attestation for the conda ecosystem </td></tr>
<tr><td> Status </td><td> Draft </td></tr>
<tr><td> Author(s) </td><td> [TBD]</td></tr>
<tr><td> Created </td><td> [TBD]</td></tr>
<tr><td> Updated </td><td> [TBD]</td></tr>
<tr><td> Discussion </td><td> [TBD]</td></tr>
<tr><td> Implementation </td><td> [conda-presto]</td></tr>
</table>

## Abstract

This CEP proposes a standard attestation layout for **conda
environment solving operations**. It defines a new in-toto
predicate type that records the inputs and observable conditions
under which a solver produced a fully-pinned environment (a
lockfile, an explicit file, or a similar resolved-environment
artifact).

This CEP is a complement to [CEP-27], which standardized publish
attestations for individual conda packages. Where CEP-27 attests
"who published this package, and to which channel," this CEP
attests "who solved this environment, from what inputs, against
what channel state, with what solver."

> The key words "MUST", "MUST NOT", "REQUIRED", "SHALL",
> "SHALL NOT", "SHOULD", "SHOULD NOT", "RECOMMENDED",
> "NOT RECOMMENDED", "MAY", and "OPTIONAL" in this document are to
> be interpreted as described in [RFC2119][RFC2119] when, and only
> when, they appear in all capitals, as shown here.

## Definitions and Concepts

This CEP assumes familiarity with the concepts defined in [CEP-27],
in particular:

- **Attestation** — a machine-readable cryptographically signed
  statement.
- **in-toto** — the framework defining the attestation Statement
  and Predicate model.
- **Sigstore** — the misuse-resistant signing scheme using
  ephemeral keys bound to identities via [Fulcio].

In addition, this CEP introduces:

- **Solver** — a piece of software that, given a set of conda
  package specifications and one or more conda channels, produces a
  fully-pinned set of packages that satisfy those specifications
  for one or more target platforms.
- **Channel snapshot** — a content-addressed reference to the
  exact state of a channel's package metadata
  (`repodata.json` and any associated metadata files) at the
  moment a solver consumed it.
- **Resolved environment artifact** — the deterministic output of
  a solve operation. Examples: a `pixi.lock`, a `conda-lock.yml`,
  a CEP-23 explicit URL list, a `conda env export` YAML.

## Motivation

The conda ecosystem currently has two well-defined trust layers:

- **Package-level trust** (CEP-27): "Alice published `numpy
  1.24.3` to `conda-forge`."
- **Channel-level trust** (operational, ad-hoc): "I trust the
  channels listed in my `.condarc`."

There is no standardized layer between these and the user's
running environment. In particular, there is no cryptographically
verifiable answer to:

- *Who* produced this lockfile?
- From *what inputs* (specs, channels, platforms)?
- Against *what channel state* (which `repodata.json` snapshot)?
- Using *what solver* (name, version, configuration)?
- *When*?

The absence of these signals causes recurring problems:

- **Lockfile rot.** A committed lockfile gives no proof it was
  ever produced cleanly, nor any way to detect that channel state
  has drifted underneath it.
- **Build reproducibility gaps.** Re-running a solve days or weeks
  later may yield a different result; without a recorded snapshot
  there is no ground truth to compare against.
- **Supply-chain visibility gaps.** Downstream consumers cannot
  determine whether a lockfile was produced by a trusted build
  pipeline or assembled by a less-trusted process.
- **CI/CD verification gaps.** Pull requests that update lockfiles
  cannot be verified against an authoritative solver identity.

Standardizing an attestation for the solve operation closes these
gaps and makes resolved-environment artifacts first-class
participants in the conda supply chain.

This is consistent with the future-work item enumerated in CEP-27:

> *Future iterations of conda's attestation design may wish to
> support and use other predicate types, such as the SLSA
> Provenance layout. Doing so would expose additional metadata
> about the package's source and build provenance, giving conda
> package consumers greater control over their consumption and
> admission policies.*

## Specification

### Attestation format

This CEP proposes the following attestation statement layout, using
the [in-toto Statement schema]:

- `predicateType` **MUST** be `https://schemas.conda.org/attestations-solve-1.schema.json`.
- `subject` **MUST** be a list of one or more [`ResourceDescriptor`]s,
  one per resolved environment artifact produced by the solve.
  - `subject[i].name` **MUST** be a stable filename for the
    resolved environment artifact (e.g.
    `linux-64.conda-lock.yml`, `pixi.lock`, `environment.lock`).
  - `subject[i].digest` **MUST** be a [`DigestSet`] containing a
    `sha256` entry with the SHA256 hash of the artifact bytes.
  - When a single solve produces per-platform artifacts, each
    artifact **SHOULD** appear as its own subject entry rather
    than being concatenated.
- `predicate` **MUST** be a JSON object with the fields described
  below.

### Predicate fields

The predicate **MUST** contain:

- `solver` (object): the solver implementation that performed the
  solve.
  - `name` (string, required): canonical solver name
    (e.g. `"rattler"`, `"libmamba"`, `"classic"`).
  - `version` (string, required): solver version string.
- `builder` (object): the entity that invoked the solver.
  - `id` (string, required): a stable identifier for the build
    entity. For an HTTP service, the canonical service URL. For a
    local CLI invocation, an opaque identifier such as the
    package name and version.
  - `version` (string, required): the version of the entity at
    invocation time.
- `request` (object): the inputs to the solve.
  - `specs` (array of strings, required): the user-requested
    package specifications, in canonical
    [`MatchSpec`](https://docs.conda.io/projects/conda-build/en/stable/resources/package-spec.html#package-match-specifications)
    string form, sorted lexicographically.
  - `channels` (array of strings, required): the channels
    consulted, in user-supplied order, normalized to canonical
    URL form (no trailing slash).
  - `platforms` (array of strings, required): the target platforms
    (subdirs) requested, sorted lexicographically.
  - `requestHash` (string, required): a SHA256 hash over the
    canonical encoding of `specs`, `channels`, `platforms`, and
    any other parameters that would change the solve output.
    Implementations MUST document their canonicalization rules.
- `channelSnapshot` (array of objects, required): the channel
  state observed by the solver, one entry per
  `(channel, subdir)` pair consulted.
  - `url` (string, required): the channel URL, normalized.
  - `subdir` (string, required): the subdir consulted (e.g.
    `linux-64`).
  - `repodataSha256` (string, required): SHA256 of the
    canonicalized `repodata.json` bytes used during the solve.
    Canonicalization rules **MUST** be documented by the
    implementation. (See *Discussion*.)
  - `fetchedAt` (string, required): RFC 3339 timestamp when the
    repodata was fetched.
- `solvedAt` (string, required): RFC 3339 timestamp when the solve
  completed.

The predicate **MAY** contain:

- `publishAttestations` (array of objects, optional): for each
  resolved package whose CEP-27 publish attestation is known to
  the builder, a backreference of the form:
  - `package` (string): the package filename.
  - `sha256` (string): the package SHA256.
  - `rekorUuid` (string, optional): the UUID of the publish
    attestation entry in the Public Good Rekor instance.
  - `bundleUrl` (string, optional): an alternative location from
    which the publish attestation bundle can be retrieved.
- `slsaBuildType` (string, optional): a SLSA-style buildType URI,
  for builders that wish to dual-encode as SLSA Provenance.

### Example

```json
{
  "_type": "https://in-toto.io/Statement/v1",
  "subject": [
    {
      "name": "linux-64.conda-lock.yml",
      "digest": {
        "sha256": "01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b"
      }
    }
  ],
  "predicateType": "https://schemas.conda.org/attestations-solve-1.schema.json",
  "predicate": {
    "solver": { "name": "rattler", "version": "0.27.0" },
    "builder": {
      "id": "https://conda-presto.jezdez.dev",
      "version": "0.X.Y"
    },
    "request": {
      "specs": ["numpy", "scipy>=1.11"],
      "channels": ["https://conda.anaconda.org/conda-forge"],
      "platforms": ["linux-64"],
      "requestHash": "3a7f0d5e6b2c..."
    },
    "channelSnapshot": [
      {
        "url": "https://conda.anaconda.org/conda-forge",
        "subdir": "linux-64",
        "repodataSha256": "8e2f1d3a4b...",
        "fetchedAt": "2026-04-16T18:30:00Z"
      }
    ],
    "solvedAt": "2026-04-16T18:30:01Z",
    "publishAttestations": [
      {
        "package": "numpy-1.26.4-py312h7f80a8c_0.conda",
        "sha256": "abcd1234...",
        "rekorUuid": "108e9186e8c5677a1b..."
      }
    ]
  }
}
```

### Signing and distributing

This CEP adopts CEP-27's signing flow verbatim:

1. The signer (a human identity, or more commonly a machine
   identity such as a CI workflow) uses a [Sigstore]-compatible
   client to generate an ephemeral keypair and bind it to their
   identity via a Fulcio-issued certificate.
2. The signer generates an in-toto statement as defined above.
3. The signer signs the statement using their ephemeral private
   key and uploads the signed attestation to the Sigstore
   transparency log (the [Public Good Instance]) as a [DSSE]
   envelope.
4. The signer produces a [Sigstore bundle] containing the
   certificate, attestation, and transparency log inclusion
   proof.

The result is a single Sigstore bundle, which **SHOULD** be
distributed alongside the resolved environment artifact. As with
CEP-27, this CEP does not mandate a specific distribution
mechanism; see *Future work*.

### Verifying

A verifier:

1. Retrieves the resolved environment artifact and its associated
   Sigstore bundle.
2. Performs a standard Sigstore verification against the bundle,
   using the expected solver identity (typically a known machine
   identity, e.g. a hosted conda-presto instance).
3. Confirms that the in-toto statement is consistent with their
   ground truth:
   - `predicateType` **MUST** equal
     `https://schemas.conda.org/attestations-solve-1.schema.json`.
   - For each subject, `subject[i].name` **MUST** match the
     filename of the artifact under verification, and
     `subject[i].digest.sha256` **MUST** match its SHA256.
   - The `predicate.builder.id` **SHOULD** match the verifier's
     expected builder identity.
4. Optionally cross-verifies any referenced
   `predicate.publishAttestations` entries against Rekor to
   establish the chain of trust from solve through publish.

### Drift detection (informative)

Verifiers MAY use the `channelSnapshot` field to detect that the
underlying channel state has changed since the solve. To do so:

1. Re-fetch the `repodata.json` for each
   `(url, subdir)` listed in `channelSnapshot`.
2. Canonicalize and SHA256-hash the response.
3. Compare against `repodataSha256`.

A mismatch indicates that re-solving with the same inputs
**may** yield a different result, and that the lockfile is no
longer guaranteed to match what the channels would currently
produce.

This is a soft signal — drift does not invalidate the
attestation, only its predictive value about future solves.

## Security Model

The unforgeability and transparency properties of this CEP follow
directly from CEP-27, since the same in-toto + DSSE + Sigstore
stack is used. In summary:

- **Unforgeability of solve provenance.** A verified attestation
  binds a signing identity to the act of producing this specific
  resolved environment from these specific inputs. An attacker
  cannot fabricate a valid attestation without compromising the
  signing identity.
- **Transparency.** Attestations are only valid when included in
  the public Rekor transparency log. Identity compromise becomes
  publicly auditable.
- **Pre-established identity trust.** As with CEP-27, this CEP
  does not eliminate the need to establish trust in signing
  identities. Verifiers must decide which solver identities they
  consider authoritative.

In addition:

- **Channel-state honesty.** The `channelSnapshot` field is
  cryptographically bound to the attestation. A solver cannot
  legitimately claim a channel state it did not observe without
  forging the attestation itself.
- **Compositional trust.** When `publishAttestations` is populated
  and verified, downstream consumers gain a cryptographically
  linked chain from "this lockfile was solved by *X*" to
  "containing package *Y*, which was published by *Z*."

## Discussion

### Relationship to CEP-27

CEP-27's publish attestation and this CEP's solve attestation are
**orthogonal layers** of the conda supply chain:

- CEP-27 attests to the act of *publishing* a package to a channel.
- This CEP attests to the act of *solving* an environment from
  packages that may themselves carry CEP-27 attestations.

Both can be present simultaneously and reinforce each other.

### Relationship to SLSA Provenance

[SLSA Provenance v1] could in principle be used directly as the
predicate. We instead define a conda-specific predicate for two
reasons:

1. **Domain fit.** SLSA Provenance is heavily oriented toward
   software *builds* (compilation, packaging). A conda solve has
   a different shape (specs in, lockfile out, channel snapshots
   are first-class). A conda-specific predicate makes that shape
   first-class.
2. **Precedent.** CEP-27 chose a conda-specific predicate over
   SLSA Provenance for the same reason. Following the same
   pattern keeps the conda attestation surface coherent.

Implementations that wish to also emit SLSA-shaped provenance
**MAY** include `slsaBuildType` in the predicate as a hint, or
emit a parallel SLSA-shaped attestation alongside the
conda-specific one.

### Channel snapshot canonicalization

The reproducibility of `repodataSha256` depends on the
canonicalization of `repodata.json` bytes prior to hashing.
Different mirror nodes, CDN edges, and historical fetch
timestamps can produce byte-different but semantically-identical
responses.

Implementations **MUST** document their canonicalization rules.
A recommended canonicalization (to be refined):

- Parse as JSON.
- Sort all object keys lexicographically.
- Use 2-space indentation, no trailing whitespace.
- Drop fields that are non-deterministic across mirrors (e.g.
  `_etag`, `_mod`).
- Re-serialize and hash with SHA256.

A future iteration of this CEP may standardize the
canonicalization rules across implementations.

### Multi-platform solves

A single conda solve commonly produces multiple per-platform
lockfiles. This CEP recommends one subject per platform. Whether
implementations emit one Statement with N subjects or N
Statements with 1 subject each is left to the implementation;
verifiers MUST handle either.

### Distribution

As with CEP-27, this CEP does not specify a distribution
mechanism. Possible mechanisms (for future work or implementer
choice):

- Sidecar file: `<artifact>.attestation.json` next to the
  artifact.
- HTTP header on the artifact response (e.g.
  `X-Solve-Attestation-Bundle`).
- A dedicated `/attestations/<sha256>` endpoint on
  attestation-aware solver services.
- Embedded in the artifact itself (where the artifact format
  permits, e.g. as a comment block in `conda-lock.yml`).

## Future work

1. **Canonicalization standard for `repodata.json` hashing.** Pin
   down a fully reproducible canonicalization across
   implementations. May warrant a separate CEP.
2. **Distribution mechanism.** As with CEP-27, leave open and
   address in a follow-up.
3. **Verification Summary Attestation (VSA).** Standardize an
   in-toto predicate for "I, *V*, verified this solve attestation
   at time *T*." Useful for audit trails and chained
   verifications.
4. **Trust roots.** A standard mechanism for advertising
   recognized solver identities (analogous to TUF roots) so that
   verifiers can configure trust without ad-hoc agreement.
5. **Per-package backreferences at scale.** Optimize
   `publishAttestations` for environments with thousands of
   packages — possibly via a Merkle commitment instead of an
   inline list.

[CEP-27]: https://github.com/conda/ceps/blob/main/cep-0027.md
[in-toto Statement schema]: https://github.com/in-toto/attestation/blob/main/spec/v1/statement.md
[`ResourceDescriptor`]: https://github.com/in-toto/attestation/blob/main/spec/v1/resource_descriptor.md
[`DigestSet`]: https://github.com/in-toto/attestation/blob/main/spec/v1/digest_set.md
[Sigstore]: https://sigstore.dev
[Fulcio]: https://github.com/sigstore/fulcio
[DSSE]: https://github.com/secure-systems-lab/dsse/blob/master/envelope.md
[Public Good Instance]: https://rekor.sigstore.dev/
[Sigstore bundle]: https://docs.sigstore.dev/about/bundle/
[SLSA Provenance v1]: https://slsa.dev/spec/v1.1/provenance
[RFC2119]: https://www.ietf.org/rfc/rfc2119.txt
[conda-presto]: https://github.com/jezdez/conda-presto

# (Draft ends)

## Submission checklist (for when this is ready)

- [ ] Polish prose; remove placeholder dates/IDs
- [ ] Confirm co-author lineup
- [ ] Generate the JSON Schema and Pydantic model
      (mirror CEP-27's `<details>` blocks)
- [ ] Open discussion thread on conda/ceps
- [ ] Open the actual PR with `cep-NNNN.md`
- [ ] Update conda-presto's [attestation](attestation.md) with the assigned CEP number
- [ ] Reference from conda-presto's README and OpenAPI description
