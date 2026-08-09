# W05 Discovery - Automation, Quality and Release Control

## Subject

- Wave: `W05-AUTOMATION-QUALITY-RELEASE`
- Tasks: `SEL-001`, `QA-001`, `REL-001`
- Approved W04 recovery ancestor: `d10d5b83c297acac8030411e20980cedf06fcd71`
- Final implementation candidate: `a241fe300676c0fcd8d31615534c2e728e02dbcb`
- Final implementation tree: `bec97b6def4fec9a7c0e0316e04b5f4c0ff160cb`

## Candidate automation discovery

- Automatic selection is a separate target-owned `candidate-decision/1.0`
  contract. Legacy ranking and edit-manifest contracts remain advisory and are
  never relabeled as an automated or human choice.
- Each candidate binds exact media bytes, a distinct immutable confidence
  receipt, adapter identity, current quality, and all four production scope
  identifiers. Selection input identity covers the complete candidate set,
  order, score, confidence, quality, policy, and context.
- Auto-selection requires every hard gate to pass, score at least 8000 basis
  points, confidence at least 8500, top margin at least 500, and current W04
  authority for the dedicated `auto_select_candidates` action. A tie, stale
  observation, hard failure, insufficient threshold, or unavailable authority
  produces stable non-authorizing escalation reasons.
- Persisted decisions are recomputed from independently supplied origin time
  and origin evidence, then separately revalidated at the current verification
  time. Complete invalid authority attempts remain exact audit provenance;
  malformed or partial evidence is rejected before an artifact is created.

## Quality discovery

- `quality-policy/1.0` defines nine closed dimensions: technical media, visual
  conformance, cinematography, motion naturalness, narrative intent,
  continuity, audio, edit rhythm, and platform compliance.
- A `quality-bundle/1.0` covers every exact media subject once per dimension.
  Media resolution may be deduplicated by subject, but every evaluator receipt
  is independently verified at both the immutable origin and current boundary.
- Technical media, continuity, platform compliance, and every safety failure
  are hard gates that cannot be averaged away.
- `remediation-plan/1.0` targets only failed shot/component/dimension tuples.
  Attempt history is contiguous, receipt-bound, replay-resistant, and capped
  at two retries. W05 defines no remediation executor.

## Release discovery

- `release-candidate/1.0` binds final media, metadata, subtitles,
  accessibility, thumbnail, QualityBundle, CandidateDecision, destination,
  policy, GateContext, workspace observation, and the four production scope
  identifiers without creating a digest cycle.
- Candidate and quality origin evidence are recomputed and current evidence is
  reverified at candidate creation and again at release assessment. The causal
  order is candidate decision, release quality, candidate creation, assessment.
- `release-assessment/1.0` distinguishes ready, approval-required, and denied.
  Initial release policy permits exactly one current one-shot human. Missing
  approval emits a non-authorizing request; campaign and self approval remain
  disabled.
- Ready means eligible for handoff only. `publish_performed=false` and
  `authority_effect=none` are invariant, and W05 defines no publisher port.

## Packaging and compatibility

- Seven additive registered artifact versions and their packaged schema copies
  extend the manifest to 80 schemas and 75 registered versions.
- The packaged quality-release resource manifest binds the target quality
  policy. Distribution version `0.3.1`, CLI command count 17, and all prior
  public identifiers remain unchanged.
- Concrete media/evaluator/confidence resolvers, durable attempt and authority
  ledgers, provider work, publication, settlement, and migration cutover remain
  W06 responsibilities.

