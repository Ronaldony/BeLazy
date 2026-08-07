# W06 — Durable runtime, managed executor and migration readiness

## Task IDs

`RUN-001`, `RUN-002`, `MIG-001`, `REL-002`

## Objective

Provide concrete local runtime boundaries outside the pure core, durable execution evidence, safe managed file mutation and a verified migration/operations path without touching real production systems.

## Durable journal

- Persist planned, dispatched, succeeded, failed, partial, uncertain and reconciled states.
- Survive process restart.
- Detect idempotency-key/request-digest mismatch.
- Reconcile uncertain external state through a port/fake.

## Managed executor

- Operate only on isolated local fixtures in tests.
- Immediately before each side effect revalidate exact bytes, authority, manifest CAS, idempotency, kill switch, containment, symlink/reparse policy and TOCTOU assumptions.
- Emit complete success/failure/partial/uncertain/reconciliation receipts.
- Never mutate source or a real production workspace.

## Migration and operations

- Promote only parity-proven legacy artifacts to read-only projections.
- Document deployment, migration, rollback, incident, drift, break-glass, journal recovery and release operations.
- Provide feature flags or reversible cutover boundaries.
- Preserve public identifiers; defer T90.

## Acceptance

- Restart, duplicate dispatch, uncertain reconciliation and partial failure tests pass.
- Path, symlink/reparse and TOCTOU defenses pass.
- Core/runtime isolation remains explicit.
- Runbooks name exact rollback and evidence requirements.
