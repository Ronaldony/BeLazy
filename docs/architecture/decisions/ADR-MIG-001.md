# ADR-MIG-001 — Read-only Blueprint shadow projections

- Status: Accepted for W06 fixture read-only migration; production cutover disabled
- Decision owner: Migration architecture
- Scope: brief, storyboard, generation, edit, sound, and publish views

## Context

Existing consumers still require legacy artifact families. Emitting projected
content directly under a legacy `artifact_version`, or allowing a caller to
mark it current, would silently cut over authority before semantic parity and
consumer migration are proven.

## Decision

All six views are wrapped in `blueprint-projection/1.0`. The envelope binds the
exact source Blueprint reference and digest, Blueprint context, compiler
identity/version/digest, target view kind, intended legacy version, payload
digest, and canonical fields.

The following values are invariant:

- `shadow_only = true`
- `read_only = true`
- `editable = false`
- `authority_effect = none`

The artifact family always remains `blueprint-projection`; the intended legacy
version is metadata inside the envelope. `ArtifactGraph` reports a blocking
finding when any projection is supplied as a current artifact. Existing
orchestration therefore cannot consume a projection as a brief, storyboard,
generation packet, edit, sound, or release authority input.

W03 shadow comparisons remain unverified diagnostic observations. They cannot
be reused as cutover evidence. W06 adds `projection-parity-receipt/1.0`.
Although its pure serializer is public, migration activation accepts only the
exact receipt bytes and reference durably registered by the injected trusted
exact-byte verifier. The repository's only verifier is an immutable
fixture-pinned corpus; it cannot infer equivalence for an unseen pair and is
explicitly disabled for production.

## Cutover rule

W06 supports a durable, read-only fixture state machine:

`legacy_only -> dual_read_compare -> projection_read_only -> rolled_back`.

Rollback may re-enter `dual_read_compare`. Every transition is hash-chained,
bound to an exact feature-flag digest and policy digest, and requires a fresh
separate activation or rollback verification. Projection selection requires a
fresh exact parity receipt for the same consumer, view, legacy bytes, projection
bytes, Blueprint source, policy, and state generation. It remains read-only and
has `authority_effect=none`.

All six production consumer entries are packaged as `unregistered`, and
`production_activation_enabled=false`. Production cutover still requires a
reviewed version-specific semantic normalizer, consumer inventory, accepted
activation decision, coexistence evidence, and rollback rehearsal. Public
identifier migration remains deferred to T90.

## Consequences

- Deterministic projections can be generated and compared immediately.
- Projection edits and stale compiler/source replay fail closed.
- Duplicate design storage remains temporarily during dual-run.
- Fixture consumers can exercise reversible read selection without changing
  ArtifactGraph currentness or production authority.

## Rejected alternatives

- Emit legacy top-level artifact families: creates a production shadow path.
- Permit projection edits: creates a second source of truth.
- Treat comparison PASS as readiness: conflates observation with authority.
