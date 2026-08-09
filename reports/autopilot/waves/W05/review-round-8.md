# W05 Review Round 8 - Derived Decision Seal

## Reviewed snapshot

- Commit: `51913286aead038b02474f654409f15913f39b6d`
- Tree: `76c18f4a6311fdd24ae12ca5fd3ee2c1f2c39929`
- Parent: `f7a0704416c91d8ddc84ff860d8595b42821e4bc`
- Verdict: `CHANGES_REQUESTED`

## Consolidated findings

All derived authorized-decision fields were recomputed through one shared
validator, and the prior evidence substitutions were closed. Final review found
one availability and trusted-port compatibility defect: the new validator
compared RFC 3339 timestamps lexically, so equal instants encoded as `Z`,
`+00:00`, or an equivalent nonzero offset were treated as rebound evidence.
Real time changes still failed closed, but canonical semantics require instant
comparison rather than representation comparison. A final narrow repair and
fresh three-role review were required.

