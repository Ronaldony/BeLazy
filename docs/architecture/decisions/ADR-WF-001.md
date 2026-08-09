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
messages, exact evidence digests, and a digest of only the material-context
fields declared by that gate. A stale PASS cannot be rebound to a changed
context; unrelated context changes invalidate only dependent claims. The evaluator returns all satisfied
claims, every blocker, the complete executable action frontier, the stable
recommended action, and the material context. It evaluates independent packet
review/feasibility and final-review/metadata branches in parallel.
Routine storyboard and packet reviews are projected into the non-human target
preflight lane. The legacy storyboard-approval claim/action identity remains
loadable for compatibility, but the target adapter satisfies that claim
without reading human approval evidence. A material creative deviation is
instead raised by ADR-AUTH-001 to an exact R4 action-authority decision.
The approval-verification context must equal the same six material digests;
missing evaluation time or any mismatch blocks approval-derived gates instead
of laundering an approval from another context.

Incremental evaluation fingerprints each gate plus the six context inputs and
invalidates the transitive dependent claims. Fingerprints contain only inputs
that the selected target mode actually reads: Rapid ignores review/approval
artifacts and evaluation time that cannot affect its preview result, while
Standard and Controlled bind the exact current approval context and time they
evaluate. `evaluate_declarative_gate_run` carries those sealed adapter-input
digests into `WorkflowEvaluation`; invalidation is never inferred later from an
unchanged `GateResult`. The observation adapter retains a
sealed prior run and skips unchanged gate helpers rather than recomputing and
relabelling them afterward. Each incremental evaluation binds the exact prior
evaluation SHA, and the predecessor chain, claim partition, and delta must
match a clean target-DAG recomputation. The ordered chain is passed intact to
plan, parity, and authority consumers; none may validate only the final hop.
After eight predecessors the caller must materialize a predecessor-free clean
evaluation. Each selected frontier action receives a distinct
`executable-production-plan/1.0` whose digest completes its seven-field
`GateContext`.

Workflow evaluation is non-authorizing. Evaluations, plans, parity reports,
legacy next-step plans, mode names, Blueprint state, and Director consensus all
carry `authority_effect=none`. Side effects require the separate ADR-AUTH-001
decision.

The legacy planner remains public during migration. A 78-row characterization
corpus runs 26 fixed legacy seed states through Rapid, Standard, and Controlled.
Rapid intentionally collapses production-ready seeds to `preview_complete`.
The legacy matrix continues to cover all 26 action identities. The target
matrix covers 25 because Standard and Controlled consolidate the legacy
`approve_storyboard` frontier into `create_generation_packet`; the dormant
legacy identity remains public and non-authorizing. Every row invokes the
actual legacy planner and a target adapter that derives every declarative gate
from the same `EpisodeStateObservation`; the adapter neither accepts an
expected action nor calls the legacy planner. Parity compares action,
blockers, actor, required authority, consumed evidence, and prohibited actions.
Explanation codes are dimension-scoped: blocker explanations accept only the
exact action-row blocker pair, and evidence-identity explanations accept only
that action row's committed legacy labels, target claim IDs, and lowercase
SHA-256 evidence. The storyboard process-consolidation explanation accepts only
the committed action, actor, authority, blocker, and evidence transition; it
cannot explain any other action or value. An unmapped blocker is always a mismatch. A code
cannot blanket-explain another dimension or an unowned value. Unexplained
dimensions fail. A parity report never applies cutover.

A canonical `WorkflowEvaluation` is not trusted merely because its self-hash
matches. Before parity comparison, executable-plan construction, or authority
request evaluation, the target
DAG is evaluated cleanly from the bundled gate results and material context;
the complete semantic projection, gate-input identities, and exact ordered
predecessor evidence must match.
Every executable plan additionally binds that exact clean evaluation digest
and exact frontier item. Structural clean recomputation proves deterministic
consistency, not truth of an external observation. Before a workflow action can
carry execution authority, ADR-AUTH-002 therefore requires the trusted ledger
to return an immutable verification reference bound to that exact evaluation
digest. Directly built or self-rehashed gate results remain non-authorizing.

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
