# W05 Review Round 7 - Canonical Authority Evidence

## Reviewed snapshot

- Commit: `f7a0704416c91d8ddc84ff860d8595b42821e4bc`
- Tree: `61c896f639cb34d36efb4f21ffa8f5be76333d1e`
- Parent: `c2c29f1af2fc4a1ed18d21218b3dc660cf76e98e`
- Verdict: `CHANGES_REQUESTED`

## Consolidated findings

The W04 canonical initial receipt binding became a shared public validator and
ordinary plus constructor-bypass receipt variants were rejected. Review found
that two derived AuthorityDecision fields were not independently recomputed:
`matched_limit_sha256` and `authority_basis_sha256`. A self-rehashed decision
could therefore survive only as invalid non-authorizing audit provenance. The
builder and evidence validator needed the same complete derived-decision check.

