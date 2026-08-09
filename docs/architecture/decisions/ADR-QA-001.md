# ADR-QA-001: Exact-media QualityBundle and bounded remediation

- Status: Accepted
- Date: 2026-08-09
- Wave: W05 (`QA-001`)

## Context

Legacy technical and continuity QC artifacts are useful observations, but they
do not prove complete independent coverage of the nine W05 quality dimensions.
A stored aggregate also cannot be allowed to hide a hard, safety, continuity,
or platform-compliance failure. Persisted references and self-consistent hashes
do not prove that media bytes or evaluator receipts are still current.

## Decision

Add a target-owned `quality-policy/1.0`, `quality-bundle/1.0`, and
`remediation-plan/1.0` contract.

- Every exact media subject binds path, byte digest, byte length, workspace
  observation digest, and a trusted observation-receipt reference.
- Each subject has exactly one evaluation for each of the nine closed quality
  dimensions. Evaluator identities and immutable evaluator-receipt references
  are distinct.
- A production verification call independently re-resolves current media and
  verifies every evaluator receipt through injected trusted runtime ports.
  Structural parsing and self-hashes never establish currentness.
- Technical media, continuity, and platform compliance are hard dimensions.
  A hard or safety failure dominates weighted aggregation.
- Remediation contains only the exact failed shot/component/dimension targets,
  never a full-pipeline wildcard. Attempt history is contiguous, receipt-bound,
  cumulative, replay-resistant, and capped at two retries. Replay,
  no-progress, regression, oscillation, or exhaustion escalates.
- All outputs have `authority_effect=none`; the runtime must separately obtain
  W04 authority before any future remediation side effect.

## Consequences

The pure core can aggregate and plan without reading files or invoking models.
Concrete media/evaluator resolvers, attempt ledgers, paid evaluation, and
remediation execution remain W06 runtime responsibilities.
