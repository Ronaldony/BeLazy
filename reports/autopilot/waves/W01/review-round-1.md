# W01 Review Round 1

## Reviewed snapshot

- Commit: `f6b63201859ceb8f2ec055e1cf3f40eeb90a64c9`
- Tree: `0725c2cd0ea855fe9e87b8dc121b3cfd4a8e689a`
- Verdict: `CHANGES_REQUESTED`

## Independent results

| Role | Critical | High | Medium | Result |
|---|---:|---:|---:|---|
| architecture/contract | 0 | 2 | 1 | changes requested |
| security/authority | 0 | 2 | 0 | changes requested |
| test/compatibility/packaging | 0 | 1 | 2 | changes requested |

The reports overlapped. The blocking categories were bounded numeric
canonicalization, authorization at the actual executor boundary, legacy
evidence validation, generation-sheet context/expiry replay, and accidental
loss of non-authorizing legacy planning order. Non-blocking findings covered
stable integer errors, order-independent plan identity, and direct wheel
metadata/RECORD regression coverage.

## Repair

Commit `791fa693a3d42fa628a27c99547b758c829229c4` added the first repair set.
It also separated structural `ready` compatibility from
`authorization_ready`, required current context/time at consuming boundaries,
and made direct executor dispatch fail before the external hook.
