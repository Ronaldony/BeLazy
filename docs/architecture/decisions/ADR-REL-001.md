# ADR-REL-001: Cycle-free release identity and one-human handoff

- Status: Accepted
- Date: 2026-08-09
- Wave: W05 (`REL-001`)

## Context

Legacy final-delivery and publish metadata do not jointly bind final media,
accessibility artifacts, thumbnail, integrated release quality, destination,
workspace observation, and current authority. Binding a ReleaseCandidate to a
decision that already consumes that same candidate would also create a digest
cycle.

## Decision

Use three additive contracts: `destination-binding/1.0`,
`release-candidate/1.0`, and `release-assessment/1.0`.

1. DestinationBinding records a non-secret target identity and schedule policy.
2. ReleaseCandidate is created first. It binds exact final media, metadata,
   subtitle and accessibility artifacts, thumbnail, a current release-scope
   QualityBundle, a cleanly reverified CandidateDecision, destination, policy,
   GateContext, exact workspace/channel/concept/episode scope, workspace
   observation, and release-intent digest. It contains
   no authority decision digest, avoiding a cycle. CandidateDecision,
   QualityBundle, media, and evaluator evidence are reverified at candidate
   creation and again at every ReleaseAssessment time. Verification receives
   the expected original creation time independently instead of trusting the
   persisted candidate's own value. The causal order is
   decision evaluation, release quality evaluation, candidate creation, then
   assessment.
3. A W04 `approve_publish` / `publish_approval` request consumes the exact
   candidate and all constituents and must match all four production-scope
   IDs. ReleaseAssessment then binds that request, risk, decision, and current
   trusted-ledger receipt.

The initial policy accepts exactly one current one-shot human release
principal. Campaign authority remains disabled. If approval is absent, core
creates only an `ApprovalRequest` with `creates_authority=false`; it never
creates human evidence. `ready` means eligible handoff, not publication:
`publish_performed=false` and `authority_effect=none` are invariant, and W05
defines no publisher port.

## Consequences

Actual upload, scheduling, credential access, pre-publish revalidation,
idempotency, journal, reconciliation, and publication remain W06 runtime work.
