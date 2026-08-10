# ADR-MIG-001 — Read-only Blueprint shadow projections

- Status: Accepted for W06 fixture read-only migration; production cutover disabled
- Decision owner: Migration architecture
- Scope: six Blueprint shadow views; fixture migration for the four views with
  registered legacy contracts

## Context

Existing consumers still require legacy artifact families. Emitting projected
content directly under a legacy `artifact_version`, or allowing a caller to
mark it current, would silently cut over authority before semantic parity and
consumer migration are proven.

## Decision

All six design views are wrapped in `blueprint-projection/1.0`. The envelope binds the
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

W06 supports a durable, read-only fixture state machine for `brief`,
`storyboard`, `generation`, and `edit`, whose exact legacy versions are present
in the installed schema registry:

`legacy_only -> dual_read_compare -> projection_read_only -> rolled_back`.

Rollback may re-enter `dual_read_compare`. Every transition is hash-chained,
bound to an exact feature-flag digest and policy digest, and requires a fresh
separate activation or rollback verification. Projection selection requires a
fresh exact parity receipt for the same consumer, view, legacy bytes, projection
bytes, Blueprint source, policy, and state generation. It remains read-only and
has `authority_effect=none`.

Generation zero also binds the exact immutable legacy artifact reference, and
every later state must preserve it. Legacy, dual-read, and rolled-back selection
accept only that anchor. A rollback from projection mode carries only the
immediately preceding activation's parity references for audit; callers do not
choose a historical rollback receipt. A rollback directly from dual-read needs
no parity receipt and still resolves the same anchored legacy artifact.

`sound` and `publish` remain design-only shadow projections. No registered
legacy `sound-manifest/1.0` or `publish-manifest/1.0` contract exists, so those
views are deliberately excluded from the migration registry and cannot mint a
parity receipt or enter the cutover state machine. The resource loader proves
that every migratable `legacy_artifact_version` resolves in the installed
schema registry and fails closed otherwise.

All four migratable production consumer entries are packaged as `unregistered`,
and `production_activation_enabled=false`. Production cutover still requires a
reviewed version-specific semantic normalizer, consumer inventory, accepted
activation decision, coexistence evidence, and rollback rehearsal. Public
identifier migration remains deferred to T90.

## Consequences

- Deterministic projections can be generated immediately; exact migration
  comparison is available only for views backed by a registered legacy contract.
- Projection edits and stale compiler/source replay fail closed.
- Duplicate design storage remains temporarily during dual-run.
- Fixture consumers can exercise reversible read selection without changing
  ArtifactGraph currentness or production authority.

## Rejected alternatives

- Emit legacy top-level artifact families: creates a production shadow path.
- Permit projection edits: creates a second source of truth.
- Treat comparison PASS as readiness: conflates observation with authority.
