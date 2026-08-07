# ADR-MUT-001 — Managed Mutation Plane and no direct human file mutation

- Status: Proposed
- Decision owner: System architecture
- Scope: production artifacts, channel/episode workspaces, policies, approvals, generated projections

## Context

The current core is intentionally plan-only, but surrounding operating procedures can still encourage humans to open files and manually add, edit, delete, move, or copy content. Manual mutation weakens provenance, exact-hash binding, reproducibility, approval validity, and automation. It also creates unobservable out-of-band state that the planner may incorrectly trust.

## Decision

Direct human mutation of managed production files is removed from the normal workflow.

Humans submit high-level intent, review semantic diffs, grant or deny action authority, and resolve exceptional ambiguity. A trusted agent/runtime converts intent into a `MutationPlan`, validates exact-before state and path policy, obtains authority, executes under a service identity, and records a `MutationReceipt` and new `WorkspaceRevision`.

The pure core plans and validates only. Concrete file mutation remains outside `src/video_factory` behind explicit runtime ports.

## Required contracts

- `change-request/1.0`
- `mutation-plan/1.0`
- `mutation-receipt/1.0`
- `workspace-revision/1.0`
- `drift-report/1.0`
- `break-glass-authorization/1.0`

## Enforcement

- human accounts are read-only in managed production workspaces by default;
- replace/delete/move require exact-before digests;
- creates fail when the destination exists;
- path traversal, workspace escape, links/reparse points, and normalization collisions are rejected;
- runtime revalidates manifest, bytes, authority, and idempotency immediately before mutation;
- out-of-band change sets workspace trust to `UNTRUSTED` and blocks generation/publish;
- emergency manual mutation is R4 break-glass with two independent approvers, short expiry, snapshot, immutable audit, and reconciliation.

## Consequences

Positive:

- reproducible and attributable mutations;
- fewer hand edits and synchronization errors;
- stronger approval and evidence binding;
- agent-first workflows that match the automation goal.

Costs:

- a managed mutation interface and runtime executor are required;
- emergency operations become slower and more formal;
- existing hand-edited workspaces require observation and reconciliation before trust.

## Rejected alternatives

- Policy text only: insufficient because it cannot detect or block out-of-band writes.
- Allow manual edits followed by best-effort validation: provenance and intent remain ambiguous.
- Put a filesystem executor in the core: violates the plan-only trust boundary.
