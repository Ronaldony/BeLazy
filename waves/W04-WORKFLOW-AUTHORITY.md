# W04 — Declarative workflow, authority control and parity

## Task IDs

`WF-001`, `AUTH-001`, `AUTH-002`, `WF-002`

## Objective

Replace the central sequential `if` chain with a versioned declarative workflow evaluator while separating assurance, autonomy and action risk and preserving legacy behavior through dual-run evidence.

## Required work

- Versioned WorkflowDefinition/DAG with claims, dependencies, gates and actions.
- Evaluator returns satisfied claims, every blocker, executable action frontier, recommended action and exact material context digests.
- Incremental invalidation when only part of the evidence graph changes.
- Common GateResult and stable reason codes.
- StandingAuthorization, AuthorityDecision, ApprovalRequest and trusted ledger port.
- Limits for capability, channel/concept, provider, destination, cost, candidates, retries, validity, revocation and kill switch.
- Distinguish AssuranceProfile, AutonomyProfile and ActionRisk.
- Do not treat AI review, consensus or mode name as execution authority.
- Dual-run legacy and declarative evaluators over characterization corpus.

## Acceptance

- No unexplained semantic difference in action, blocker, actor, required authority, consumed evidence and prohibited actions for covered legacy cases.
- Stale/revoked/mismatched grants fail closed.
- Policy fields have an enforcement matrix and tested owner.
- Legacy next-step projection remains available during migration.
