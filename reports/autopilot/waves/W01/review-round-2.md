# W01 Review Round 2

## Reviewed snapshot

- Commit: `791fa693a3d42fa628a27c99547b758c829229c4`
- Tree: `9c6cd12034c349255a0d280d58e20460ed9ba6ad`
- Verdict: `CHANGES_REQUESTED`

## Results and execution note

- The security/authority reviewer reported Critical 0, High 1, Medium 1.
  Authorization needed a canonical digest of every request-envelope field;
  the public authority API also needed to require `GRANTED` unconditionally.
- The architecture reviewer independently identified external reconciliation
  as an additional side-effect boundary requiring the same current
  authorization and exact original request/reference binding.
- Two final reviewer messages were rejected by the platform output filter.
  The partial findings were retained; no approval was inferred from the tool
  errors. All three roles were rerun against the next exact commit.

## Repair

Commit `075e2e108ba8eb4122a3f79c4d0e9e195b21bedb` added:

- canonical `RequestEnvelope` SHA-256 in `OrchestrationAuthorization`;
- dispatch-time exact-request equality;
- authorization-protected reconciliation bound to the original unresolved
  request and external reference;
- unconditional `GRANTED` evidence semantics;
- stable Decimal runtime-exponent errors; and
- updated public contract documentation and regression tests.
