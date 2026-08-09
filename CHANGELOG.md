# Changelog

## Unreleased

- Added W05 exact-media quality, automatic selection, and release-handoff
  contracts. QualityBundle requires nine independent dimensions and trusted
  current media/evaluator receipts; hard and safety failures dominate scores,
  while remediation remains affected-target-only with receipt-bound bounded
  history.
- Added confidence/score/margin-bound CandidateDecision with current W04
  trusted-ledger verification. Legacy candidate-ranking/edit-selection truth
  semantics remain unchanged; the compatibility projection is read-only,
  non-current, and never claims human selection.
- Added cycle-free DestinationBinding, ReleaseCandidate, and ReleaseAssessment.
  Initial release retains exactly one current human approval, creates no human
  evidence, performs no publish action, and leaves campaign activation and all
  runtime upload/reconciliation work to W06. Added seven closed schemas and an
  exact packaged quality-policy resource, bringing the wheel registry to 80
  schemas and 75 registered artifact versions.
- Bound W04 incremental invalidation to sealed, mode-material adapter inputs:
  Rapid skips irrelevant review/approval/time changes, while current Standard
  and Controlled approvals invalidate their exact dependent claims. Replaced
  the routine storyboard human checkpoint with non-human integrated preflight,
  recorded the exact two-row legacy parity consolidation, and escalated known
  material creative deviations to short-lived R4 dual-human authority while
  UNKNOWN classifier facts remain denied. Final review hardening rejects an
  unsupported current risk before any pre-dispatch ledger reservation and
  binds cached gate reuse to a non-copyable run seal plus the exact predecessor
  inputs and results.
- Closed W04 fail-closed review gaps: actual gate-helper reuse with exact
  predecessor-chain invalidation and ordered plan/parity/authority consumption,
  an eight-predecessor clean-rebase boundary, action-row-specific parity normalization,
  trusted receipts for POLICY workflow authority, a 300-second R4 ceiling,
  role-typed ledger/authentication/signature evidence, and exact W02/W04
  mutation request-envelope plus idempotency binding before reservation.
- Added the W04 target-owned declarative workflow plane: 24 versioned claims
  and gates, 26 legacy action identities, complete blocker/frontier evaluation,
  parallel review opportunities, action-specific material-context plans, and
  transitive incremental invalidation with full-recompute equivalence.
- Hardened W04 review findings: real same-observation dual-run adapters,
  gate-local context binding, exact evaluation/frontier plan binding,
  dimension-scoped parity explanations, requester/self-approval separation,
  one content-distinct signature verification per human, authenticated
  requester binding, trusted workflow-evaluation verification evidence,
  immutable authority-basis sealing, and exact runtime executor scope checks.
- Added non-authorizing dual-run parity over all 26 actions and three workflow
  modes. Reports compare action, blockers, actor, required authority, consumed
  evidence, and prohibited actions; unexplained differences fail and no report
  applies cutover.
- Added the authority control plane with separate assurance, autonomy, and risk;
  exact action/scope requests; a target-owned policy and enforcement matrix;
  parse-only standing grants; non-authorizing approval requests; ledger-backed
  decisions; and fresh purpose-bound dispatch, reconcile, and mutation
  revalidation/reservation receipts.
- Bound every target policy field to a distinct executable positive and
  negative conformance-test node. Closed hard-escalation facts now cover the
  exact target catalog with immutable evidence; triggered facts escalate to
  R4 dual-human authority and UNKNOWN facts remain denied. Workflow approval context must exactly equal the six
  shared declarative material digests, while incremental invalidation remains
  gate-local and full-recompute equivalent.
- Legacy approvals, readiness, sheets, modes, AI reviews, Director results, and
  Blueprints can no longer self-assert execution authority. Generation sheets
  are always previews. Managed mutation remains fixed R4 and now requires the
  W04 dual-human ledger decision in addition to its existing exact break-glass
  evidence.
- Added 12 closed workflow/authority schemas (73 packaged schemas, 68 registered
  versions), exact packaged workflow/policy/parity resources, semantic resource
  verification in offline wheels, and public `video_factory.workflow` and
  `video_factory.authority` packages. Concrete ledger/executor persistence
  remains deferred to W06.
- Added the W03 ProductionBlueprint shadow plane with nine packaged artifact
  contracts, complete root/per-shot design validation, exact field ownership
  and distinct verification, material-context binding, and strict typed JSON
  round trips.
- Added a target-owned logical Director Mesh: twelve core and six conditional
  charters, deterministic activation, parallel task plans, model/prompt/receipt
  bound assessments, and deterministic conflict synthesis capped at two rounds.
- Added deterministic brief/storyboard/generation/edit/sound/publish shadow
  views inside the read-only `blueprint-projection/1.0` envelope. Projections
  and diagnostic comparisons have no authority effect, and current projection
  snapshots now fail the ArtifactGraph gate. Caller-asserted legacy fields are
  labeled unverified diagnostics rather than parity evidence. Legacy authority
  paths are not cut over in W03.
- Bound Blueprint task/projection references to exact canonical artifact bytes,
  while retaining separate logical identity digests. Coherent promotion now
  requires material typed detail and complete Director provenance; task and
  synthesis consumers revalidate the exact Channel/Concept/EpisodeIntent chain
  and re-derive conditional activation. The general builder cannot mint a
  coherent state; persisted promotion claims require complete synthesis-evidence
  recomputation. Reference locks reject duplicate, colliding, or conflicting
  identities through a shared canonical path policy. Production mesh entry
  points are anchored to the exact target-owned 12-core/6-conditional registry
  and draft bases. Conflict round two re-synthesizes the complete task and
  assessment evidence for its exact blocked predecessor.
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
