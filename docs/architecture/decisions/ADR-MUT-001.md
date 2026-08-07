# ADR-MUT-001 — Managed Mutation Plane and no direct human file mutation

- Status: Accepted (W02 contract and guard; concrete runtime deferred to W06)
- Decision owner: System architecture
- Scope: production artifacts, channel/episode workspaces, policies, approvals, generated projections

## Context

The current core is intentionally plan-only, but surrounding operating procedures can still encourage humans to open files and manually add, edit, delete, move, or copy content. Manual mutation weakens provenance, exact-hash binding, reproducibility, approval validity, and automation. It also creates unobservable out-of-band state that the planner may incorrectly trust.

## Decision

Direct human mutation of managed production files is removed from the normal workflow.

Humans submit high-level intent, review semantic diffs, grant or deny action authority, and resolve exceptional ambiguity. A trusted agent/runtime converts intent into a `MutationPlan`, validates exact-before state and path policy, obtains authority, executes under a service identity, and records a `MutationReceipt` and new `WorkspaceRevision`.

The pure core plans and validates only. Concrete file mutation remains outside `src/video_factory` behind explicit runtime ports.

W02 implements this boundary additively as `video_factory.mutation` plus
`video_factory.providers.ManagedMutationExecutorPort`. It does not add a
filesystem executor, durable journal implementation, or human-approval issuer.
W04 supplies the trusted authority-ledger port and exact decision/receipt
contracts; concrete durable ledger and executor implementations remain W06
responsibilities.

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
- runtime revalidates the exact `GateContext`, plan digest, policy digest,
  workspace revision, observation digest, service identity, and kill switch;
- request-declared risk is never authoritative. W02 has no trusted semantic
  classifier evidence, so every mutation (including every CREATE path) is
  conservatively R4. W04 now binds a current classifier decision to the exact
  request, target policy, seven-digest context, workspace, and side-effect
  purpose, but its target policy deliberately keeps `managed_mutation` at R4.
  R1/R2/R3 activation remains disabled until a later trusted semantic
  classifier is explicitly adopted. W04 authority remains required and R4
  break-glass is additive;
- create/replace content is re-resolved by a trusted port and exact object ID,
  digest, and byte length are bound into the execution authorization; identical
  objects reused across operations are resolved once and conflicting reuse of
  one object ID is rejected before idempotency reservation; each unique object
  retains its resolver-evidence pair and consumers require exact set coverage;
- idempotency is an atomic trusted reservation over key, exact plan, and exact
  workspace observation; a caller-supplied optional lookup cannot authorize;
- out-of-band change sets workspace trust to `UNTRUSTED` and blocks generation/publish;
- generation, publish, dispatch, reconcile, and mutation gates compare the
  complete observed file set with a canonical trusted revision artifact and
  bind workspace identity as well as revision/manifest strings;
- emergency manual mutation is R4 break-glass with two independent approvers
  whose trusted ledger records bind the exact canonical request scope, short
  expiry, verified snapshot/incident/audit evidence, and reconciliation;
- a successful receipt binds the exact execution authorization, pre/post
  workspace observations, idempotency reservation, journal, and service
  identity; immutable revision promotion independently rechecks the parent
  revision digest and every resulting active file.
- canonical authorization IDs are integrity identifiers, not authority. Every
  consuming boundary uses the plan-aware validator so removing content or R4
  evidence and recomputing an ID cannot authorize execution or revision
  promotion.
- baseline and managed lineage cannot be conflated: genesis revisions require
  explicit reconciliation evidence, while managed revisions require a parent.

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
