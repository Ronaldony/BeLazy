# ADR-WF-001 - Target-owned declarative workflow and dual-run parity

- Status: Accepted for W04 declarative evaluation; legacy cutover deferred
- Decision owner: Workflow architecture
- Scope: production claims, gates, actions, incremental evaluation, legacy parity

## Context

The legacy orchestrator is a deterministic sequential `if` chain. It remains a
useful compatibility oracle, but it cannot directly expose independent work,
complete blockers, or dependency-scoped invalidation. Replacing it in one step
would risk silently changing actor, authority, evidence, and prohibited-action
semantics.

## Decision

The production workflow is represented by the exact target-owned
`episode-production-workflow/1.0` definition. It contains versioned claims,
dependencies, gates, and actions. Production evaluation requires that exact
definition; a caller cannot replace it with a self-consistent reduced or
re-versioned graph.

Every gate produces `gate-result/1.0` with a stable status, reason codes,
messages, and exact evidence digests. The evaluator returns all satisfied
claims, every blocker, the complete executable action frontier, the stable
recommended action, and the material context. It evaluates independent packet
review/feasibility and final-review/metadata branches in parallel.

Incremental evaluation fingerprints each gate plus the six context inputs and
invalidates the transitive dependent claims. Its semantic result must equal a
clean full recomputation. Each selected frontier action receives a distinct
`executable-production-plan/1.0` whose digest completes its seven-field
`GateContext`.

Workflow evaluation is non-authorizing. Evaluations, plans, parity reports,
legacy next-step plans, mode names, Blueprint state, and Director consensus all
carry `authority_effect=none`. Side effects require the separate ADR-AUTH-001
decision.

The legacy planner remains public during migration. A 78-row characterization
corpus runs 26 fixed legacy seed states through Rapid, Standard, and Controlled.
Rapid intentionally collapses production-ready seeds to `preview_complete`; the
committed expected-action matrix records that policy difference, while the
union of the corpus covers all 26 action identities. Every row invokes the
actual legacy planner and declarative evaluator. Parity compares action,
blockers, actor, required authority, consumed evidence, and prohibited actions.
Only explicitly allowlisted explanations can account for a difference;
unexplained dimensions fail. A parity report never applies cutover.

A canonical `WorkflowEvaluation` is not trusted merely because its self-hash
matches. Before parity comparison or executable-plan construction, the target
DAG is evaluated cleanly from the bundled gate results and material context;
the complete semantic projection and gate-input identities must match.

## Consequences

- Independent work is visible without adding sequential human stages.
- Material changes invalidate only affected claims while preserving full-run
  equivalence.
- Legacy compatibility remains observable and reversible.
- W04 does not remove the legacy branch or claim that dual-run evidence has
  completed a production cutover.

## Rejected alternatives

- Immediate replacement of the legacy planner: lacks bounded compatibility
  evidence and rollback.
- Caller-supplied workflow definitions in production: allows self-consistent
  removal of required gates.
- Treating frontier readiness as authority: conflates planning with permission.
