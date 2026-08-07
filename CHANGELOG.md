# Changelog

## Unreleased

- Added the W03 ProductionBlueprint shadow plane with nine packaged artifact
  contracts, complete root/per-shot design validation, exact field ownership
  and distinct verification, material-context binding, and strict typed JSON
  round trips.
- Added a target-owned logical Director Mesh: twelve core and six conditional
  charters, deterministic activation, parallel task plans, model/prompt/receipt
  bound assessments, and deterministic conflict synthesis capped at two rounds.
- Added deterministic brief/storyboard/generation/edit/sound/publish shadow
  views inside the read-only `blueprint-projection/1.0` envelope. Projections
  and parity comparisons have no authority effect, and current projection
  snapshots now fail the ArtifactGraph gate. Legacy authority paths are not cut
  over in W03.
- Added the pure managed-mutation contract and guard plane: six packaged
  artifacts, deterministic exact-before planning, immutable revision and
  tombstone derivation, drift quarantine, strict cross-platform path policy,
  trusted content resolution, and atomic idempotency reservation ports.
- Made caller-declared risk non-authoritative. W02 conservatively raises every
  mutation to R4 because no trusted semantic classifier exists yet; activating
  R1/R2/R3 is deferred to W04 and requires an exact policy-bound decision.
  Every mutation therefore requires normal authority plus two distinct current
  human approvals bound to the exact break-glass request and verified snapshot,
  incident, and audit evidence.
- Bound execution receipts and downstream generation/publish/adapter gates to
  canonical workspace revisions, exact workspace identity and file bytes,
  execution authorization, pre/post observations, reservation, journal, and
  recovery evidence. The core remains side-effect free; the concrete executor
  and durable journal remain runtime responsibilities.

## 0.3.1 - 2026-07-30

- **Behavior change:** failed INFO constraints in single-shot QC now keep both
  the per-constraint and overall judgment at `PASS` instead of `WARN`, matching
  continuity QC. The unsuccessful constraint remains recorded as
  `Finding.passed = false` with an explicit informational message.
- INFO findings no longer contribute to `warn_count`; they contribute to
  `pass_count`, keeping counts consistent with the returned judgment.
- Hardened media-match status selection to inspect explicit `error` and
  `warning` severities. Existing error/warning behavior is unchanged, while a
  future informational finding will not be treated as a warning merely because
  a finding exists.

## 0.3.0 - 2026-07-28

- Added `generation-packet/2.1`, which declares per-shot first-frame
  `depicted_elements` and `continuity.carried_elements`. `2.0` stays accepted.
- Added the `first_frame_state_carryover` feasibility check: a shot that claims
  to inherit an element must name an earlier shot in the same packet and depict
  that element in its own first frame. Completeness is derived from the
  hash-bound storyboard source shot's `end_state_elements`; missing source
  evidence is inconclusive and an unmatched empty declaration fails. Not
  emitted for `2.0` packets.
- Added `video_factory.continuity` and `continuity-qc/1.0`: cross-shot
  comparison of one opaque element between two generated clips on
  relative-scale, orientation/shape, and presence axes. Missing observations are
  inconclusive; tolerances are caller-supplied, finite, and nonnegative.
- Preserved measurement values/units and all plan policy inputs in serialized
  continuity QC, with pure rejudgment from the stored document.
- Added current continuity QC to multi-shot orchestration before candidate
  ranking: reports must pass/warn, cover every adjacent last-to-first pair on
  all three axes, and exactly hash-bind current generated shot outputs.
- Accepted both packet 2.0 (six feasibility checks) and 2.1 (seven checks) in
  generation readiness, rejected duplicate packet shot IDs in standalone strict
  feasibility, and replaced ambiguous dotted continuity IDs with canonical
  structured hashes.
- Made all schema-allowed measurement value forms exception-safe. Non-finite,
  nonpositive, nonnumeric, boolean, and null relative-scale observations are
  consistently inconclusive.
- Clarified that `continuity_anchor` proves only that an anchor was declared.
  Whether a generator preserved that anchor is decidable only after generation,
  through `continuity-qc/1.0`.

## 0.2.0 - 2026-07-23

- Added validated, hash-bound artifact graphs and deterministic next-step IDs.
- Added strict generation packet v2 and independent feasibility review.
- Separated pending approval requirements from granted human evidence.
- Closed stale/failed review, generation, per-shot QC, final review, and
  publish-approval gates.
- Made generation sheets require a bound readiness proof.
- Added QC warning/applicability/fallback semantics.
- Added separate-audio mux planning, post-encode checks, and fallback profiles.
- Added pure double-extension media matching and injected aspect validation.
