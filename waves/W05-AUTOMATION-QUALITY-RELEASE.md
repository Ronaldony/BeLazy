# W05 — Candidate automation, QualityBundle and release control

## Task IDs

`SEL-001`, `QA-001`, `REL-001`

## Objective

Remove routine human candidate selection and repetitive QC/release steps while preserving strict escalation and authority boundaries.

## CandidateDecision

Automatically select only when:

- all hard quality gates pass;
- no safety/continuity failure exists;
- score, confidence and top-candidate margin meet policy thresholds;
- QC is bound to exact media bytes and current context;
- action is covered by current authority.

Otherwise emit stable escalation reason codes. Keep a compatibility projection for legacy human-selection contracts.

## QualityBundle

Aggregate independent evaluators for technical media, visual conformance, cinematography, motion naturalness, narrative intent, continuity, audio, edit rhythm and platform compliance. Hard failures cannot be averaged away. Remediation targets only affected shots/components and has bounded retries.

## Release control

- ReleaseCandidate binds final media, metadata, subtitle/accessibility, thumbnail, quality, policy, destination and authority.
- ReleaseAssessment distinguishes ready, approval-required and denied.
- Do not actually publish.
- Initial migration may retain one final human release approval, represented as a requirement—not synthesized evidence.

## Acceptance

- Auto-select, tie/low-margin, low-confidence, hard-failure and stale-QC cases are tested.
- Quality aggregation preserves hard failures.
- Targeted remediation avoids full-pipeline rerun.
- Release destination/context mismatch fails closed.
