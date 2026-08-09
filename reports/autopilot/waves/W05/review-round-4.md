# W05 Review Round 4 - Immutable Quality Timelines

## Reviewed snapshot

- Commit: `4f30d9b8b0db5d34dd4a6a19f81de49fca15552d`
- Tree: `79d0c4442fad4f0a906a73246c57fb1a8f51a1ff`
- Parent: `5bb83e8f639422773414c22b7ac02e7ddd4c708f`
- Verdict: `CHANGES_REQUESTED`

## Consolidated findings

Independent CandidateDecision and QualityBundle origin times closed the prior
findings. Review then found that a builder-produced escalation based on a
complete but wrong-scope authority pair could never cleanly round-trip through
its verifier. Valid authority digests were stored only for successful
authorization, while the verifier either rejected the original invalid pair or
recomputed a different missing-authority reason. The audit artifact was
non-authorizing, but public builder/verifier closure required another repair.

