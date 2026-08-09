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
  confidence are integer basis points and ordering is canonical.
- Auto-selection requires the target quality policy, a fully current verified
  QualityBundle, no hard/safety/inconclusive blocker, a unique top candidate,
  score at least 8000, confidence at least 8500, and margin at least 500 basis
  points. Equality passes; any lower value or a tie escalates with stable
  reason codes.
- The decision revalidates the exact W04 `rank_generation_candidates` /
  `rank_candidates` request and trusted-ledger result. Caller-supplied receipt
  fields or a self-rehashed document are insufficient.
- A downstream consumer must cleanly recompute a persisted decision from its
  original QualityBundle, candidate set, current resolvers, and authority
  evidence.
- The legacy projection is read-only, non-current, and non-authorizing. It
  never emits an edit manifest or `selected_by_human=true`.

## Consequences

W05 introduces an additive automation plane without changing legacy artifact
truth semantics or the W04 78-row compatibility oracle. Legacy orchestration
cutover and removal of the human-selection branch remain a W06 migration
decision after runtime and parity evidence exist.
