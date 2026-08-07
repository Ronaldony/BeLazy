# W02 Review Round 2

## Reviewed snapshot

- Commit: `ebbc3fbf031080a31abf4375e7423a545c0b31db`
- Tree: `3dc4f33add73a5dde47dada573587df422dbbf1c`
- Parent: `de2fa08e565cb0fc6d194461d10263ef46adc422`
- Verdict: `CHANGES_REQUESTED`

## Independent results

| Role | Critical | High | Medium | Result |
|---|---:|---:|---:|---|
| architecture/contract | 0 | 2 | 2 | changes requested |
| security/authority/boundary | 0 | 1 | 1 | changes requested |
| test/compatibility/packaging | 0 | 1 | 1 | changes requested |

The reports converged on two blocking classes: the versioned risk classifier
still lacked trusted inputs for lower tiers, and a structurally self-consistent
execution authorization could omit plan-required R4 or content evidence before
trusted revision promotion. Medium items covered Windows console devices,
duplicate content resolution after reservation, and drift validation of length
and origin invariants.

## Repair

Commit `56170d0865cb8153fd53e94598cb99e4ea7db3d4` made all W02 mutation plans R4
until W04, required plan-aware authorization validation at consumers, added the
missing reserved-device rules, canonicalized unique content resolution before
reservation, and made drift validation fail closed on invalid revisions,
observations, and byte lengths.
