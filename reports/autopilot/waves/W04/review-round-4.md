# W04 Review Round 4 - Target Preflight and Input Binding

## Reviewed snapshot

- Commit: `1e8aeae53eef1099046096575e282184622b869d`
- Tree: `c9d52916f70a5eea54e8790b3f9a9549d0d8b192`
- Parent: `34ea16d457e9efc949200acf170eaf7cc1150bf5`
- Verdict: `CHANGES_REQUESTED`

## Independent results

| Role | Critical | High | Medium | Result |
|---|---:|---:|---:|---|
| architecture/contract | 0 | 0 | 1 | CHANGES_REQUESTED |
| authority/contract | 0 | 1 | 0 | CHANGES_REQUESTED |
| test/compatibility/packaging | 0 | 0 | 0 | GO |

Acceptance review confirmed the exact 78-row dual run, 943-test full suite,
mode-aware helper invocation counts, routine non-human storyboard preflight,
R4 dual-human escalation, 34-row policy matrix, boundary checks, 73/68 schema
resources, and deterministic 203-member wheels.

Architecture review reproduced a stale cache path: a copied DeclarativeGateRun
could carry rebound current input digests while reusing prior results. Authority
review reproduced a separate pre-side-effect gap: an UNKNOWN, unsupported
current risk was not rejected before fresh ledger reservation. Both findings
were repaired in a new exact candidate; this round remained NO-GO.

