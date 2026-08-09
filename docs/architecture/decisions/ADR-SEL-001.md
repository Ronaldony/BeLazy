# ADR-SEL-001: Current-authority automatic candidate selection

- Status: Accepted
- Date: 2026-08-09
- Wave: W05 (`SEL-001`)

## Context

`candidate-ranking/1.0` is a deterministic legacy ranking document, not an
automatic choice contract. `edit-manifest/1.0` records a human selection and
must not be relabeled to make an automated choice appear human. Automatic
selection is safe only when exact current quality, confidence, score margin,
and current W04 authority all agree.

## Decision

Add the separate `candidate-decision/1.0` contract.

- A shot needs at least two unique exact-media candidates. Scores and
  confidence are integer basis points and ordering is canonical. Every
  confidence value is bound to a distinct immutable
  `candidate-confidence-receipt/1.0` and is reverified through a trusted
  current-confidence port.
- Auto-selection requires the target quality policy, a fully current verified
  QualityBundle, no hard/safety/inconclusive blocker, a unique top candidate,
  score at least 8000, confidence at least 8500, and margin at least 500 basis
  points. Equality passes; any lower value or a tie escalates with stable
  reason codes.
- Legacy `rank_generation_candidates` / `rank_candidates` remains advisory.
  Automatic choice uses the separate target-owned non-workflow
  `auto_select_candidates` action/capability. Its W04 request envelope equals
  the exact selection-input digest; that digest and the persisted decision
  bind workspace, channel, concept, and episode IDs. Its scope includes those
  exact four IDs plus every media and confidence-receipt reference.
  Caller-supplied receipt fields or a self-rehashed document are insufficient.
- A downstream consumer must cleanly recompute a persisted decision from its
  original QualityBundle, candidate set, original ledger authority evidence,
  and current resolvers. Only after the complete original artifact matches may
  it separately apply freshly evaluated authority evidence. The immutable
  decision timestamp is supplied independently to verification and remains
  separate from the trusted current-verification timestamp even when no
  authority was issued; selection cannot predate its QualityBundle.
- Authority evidence and its trusted ledger are an indivisible input pair.
  Complete evidence that fails scope or current-authority validation remains
  non-authorizing, but its request, decision, and receipt digests are retained
  as exact attempt provenance so the resulting escalation can be reproduced
  and audited. Partial evidence is rejected instead of being collapsed into a
  generic missing-authority decision.
- The legacy projection is read-only, non-current, and non-authorizing. It
  never emits an edit manifest or `selected_by_human=true`.

## Consequences

W05 introduces an additive automation plane without changing legacy artifact
truth semantics or the W04 78-row compatibility oracle. Legacy orchestration
cutover and removal of the human-selection branch remain a W06 migration
decision after runtime and parity evidence exist.
