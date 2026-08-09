# W04 Review Round 2 - Authority and Runtime Evidence Repair

## Reviewed snapshot

- Commit: `a3d99ff3b064dd98482caca1af42c87dd50a1b9b`
- Tree: `167fc12227d26db2612b9c7635903fca08d86d43`
- Parent: `ced5a5d7c0fdb2cc30903f8d4fbdc6475aa5c2e5`
- Verdict: `CHANGES_REQUESTED`

## Consolidated findings

The repair closed requester/signature, immutable authority-basis, exact runtime
scope, and current-context gaps. Review still found that the adapter recomputed
all gates and labeled them reused only afterward, enforcement-matrix owner and
phase claims were not executed at their real boundary, parity explanations
could accept globally known but row-unowned values, mutation and W04
idempotency identities could diverge, and R4 expiry and evidence-role contracts
were incomplete.

The candidate was not accepted. No Critical finding was recorded, but unresolved
High findings required another repair and fresh review.

