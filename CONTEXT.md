# Conda-presto

Conda-presto exposes conda solving, parsing and artifact operations through an HTTP service so other systems can use them without embedding conda.

The current scope includes registered exporters, restricted lockfile conversion, bounded retained outputs, and optional SBOM generation and artifact signing and verification. A small CLI supports direct execution and starting the server.

Detailed solve construction evidence remains deferred. Its requirements below and in ADRs 0001 and 0002 remain accepted design constraints for any future implementation. The current output-signing step authenticates saved bytes and a signer, without capturing the solve's inputs or proving its construction.

## Language

**Solve artifact**:
The resolved output of a solve, which can be retained and reused without repeating that solve.

**Solve provenance**:
A record of how a solve artifact was constructed, preserving the request and construction context for a recipient who did not observe the original solve.

**Solve attestation**:
An authenticated statement by an identified producer describing the construction of a solve artifact and bound to that artifact's exact contents.

**Solve profile**:
A documented definition of solve construction claims, their representation in an attestation, and how a recipient verifies them.

**Solve producer**:
The party responsible for the execution described in a solve attestation.

**Producer trust policy**:
The recipient's rules for which solve producer identities and identity issuers it accepts.

**Evidence check**:
An assessment against a stated requirement that identifies the evidence examined, the outcome, and any missing evidence. Framework requirements and organization-selected expectations remain distinguishable.

**Solver index input**:
The metadata actually supplied to the solver's index when constructing a solution, including any transformed representation that is consumed.

**Release producer**:
The party responsible for constructing and publishing a product release and recording how its inputs relate to what was shipped.
