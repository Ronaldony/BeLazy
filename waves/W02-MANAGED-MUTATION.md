# W02 — Managed Mutation Plane

## Task IDs

`MUT-001`, `MUT-002`, `MUT-003`, `MUT-004`

## Objective

Eliminate direct human mutation as the normal managed-file workflow while retaining a pure planning core and explicit runtime execution boundary.

## Required contracts

- `change-request/1.0`
- `mutation-plan/1.0`
- `mutation-receipt/1.0`
- `workspace-revision/1.0`
- `drift-report/1.0`
- `break-glass-authorization/1.0`

## Required behavior

- Deterministic pure planner with stable IDs and reason codes.
- Exact-before digest for replace/delete/move.
- Immutable revisions for add/replace and tombstones for normal logical removal.
- Workspace revision and manifest compare-and-swap.
- Path containment, traversal, absolute path, symlink/reparse, case and Unicode collision defenses.
- Out-of-band drift marks workspace untrusted and invalidates dependent plans/authority/QC.
- Runtime port and pre-side-effect guard outside pure core.
- R4 break-glass requires two distinct authenticated human approvers, narrow scope, expiry, snapshot, immutable audit and reconciliation.
- AI can validate evidence but cannot create human approval.

## Acceptance

- Contracts are packageable, registered and round-trip validated.
- Planner is deterministic and side-effect free.
- Negative path, stale digest, drift, idempotency and break-glass tests pass.
- Core purity and side-effect checks remain green.
