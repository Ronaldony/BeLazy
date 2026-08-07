# ADR-MIG-001 — Read-only Blueprint shadow projections

- Status: Accepted for W03 dual-run; authority cutover deferred
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

Shadow comparisons bind both immutable references, comparator and normalization
rule digests, exact projected/observed value hashes, and have no authority
effect. A parity match is evidence for migration analysis only.

## Cutover rule

W03 does not change legacy currentness, readiness, execution, or release paths.
A later migration must inventory consumers, prove semantic parity, version any
public contract change, provide coexistence and rollback, and receive a
separate accepted decision before cutover.

## Consequences

- Deterministic projections can be generated and compared immediately.
- Projection edits and stale compiler/source replay fail closed.
- Duplicate design storage remains temporarily during dual-run.

## Rejected alternatives

- Emit legacy top-level artifact families: creates a production shadow path.
- Permit projection edits: creates a second source of truth.
- Treat comparison PASS as readiness: conflates observation with authority.
