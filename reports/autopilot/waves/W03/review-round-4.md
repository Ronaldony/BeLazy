# W03 Review Round 4 — Changes Requested

## Reviewed snapshot

- Commit: `7c7eefad23c6841be03a966b1a1c9083bea113e7`
- Tree: `47ce5aa54235691501f97fe0d0624fbe314fa4cf`
- Parent: `acefde0a42f2e4bf30702ee7532126f5ab69cc69`
- Verdict: `CHANGES_REQUESTED`

## Consolidated findings

- Registry anchoring, draft-only bases, recursive predecessor evidence,
  canonical path aliases, coherent promotion, and shadow authority isolation
  were closed.
- The shared reference-path validator still rejected only code points below
  U+0020. It accepted ASCII DEL and Unicode C1 controls, allowing invisible
  immutable identities in Blueprint locks, Director task inputs, projections,
  and observations.

The validator was changed to reject every Unicode `Cc` character and negative
coverage was added on all three reference consumers in
`c48ef226e8abb226a6bbfe679972154fc9cd9f55`.

