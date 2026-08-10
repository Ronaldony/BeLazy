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

## Implemented boundary

- Pure contracts and strict serializers live in `video_factory.runtime`.
- Concrete adapters live only in `video_factory_runtime` and require a newly
  initialized marker-bound fixture root. Packaged policy fixes
  `fixture_only=true` and `production_enabled=false`.
- SQLite provides atomic idempotency claims, FULL synchronization, append-only
  hash-chain events, stable reservation claims, append-only fresh authority
  verification history, immutable terminal receipt history, restart loading, and distinct
  resume/reconcile state handling. The exact ordered mutation-effect IDs are
  part of the canonical `ExecutionIntent`, and dispatch revalidates both the
  execution row and complete event chain inside its transaction.
- Managed mutation, synthetic external execution, and fake publication capture
  fresh W04 reservation and settlement evidence. W05 `ready` remains
  non-authorizing; publication uses a separate R3 side-effect action. POSIX
  mutation uses a complete no-follow directory-descriptor chain and only
  handle-relative `*at` operations. Windows keeps exact ancestor, directory,
  and file handles live and rejects a directory-path rebound before use.
- Migration accepts only exact pinned fixture parity plus separate current
  activation/rollback evidence. Generation zero permanently anchors the exact
  legacy reference, including direct dual-read rollback. Every production consumer is unregistered and
  public identifier migration remains deferred.
- Deployment, migration, rollback, incident, drift, break-glass, journal
  recovery, and release procedures are under `docs/runbooks/`.
