# Conda-presto

Conda-presto exposes conda solving, parsing and artifact operations through an HTTP service so other systems can use them without embedding conda.

## Language

**Solve artifact**:
The resolved output of a solve, which can be retained and reused without repeating that solve.

**Solve provenance**:
A record of how a solve artifact was constructed, preserving the request and construction context for a recipient who did not observe the original solve.

**Solve attestation**:
An authenticated statement by an identified producer describing the construction of a solve artifact and bound to that artifact's exact contents.

**Solve producer**:
The party responsible for the execution described in a solve attestation.

**Producer trust policy**:
The recipient's rules for which solve producer identities and identity issuers it accepts.

**Solver index input**:
The metadata actually supplied to the solver's index when constructing a solution, including any transformed representation that is consumed.
