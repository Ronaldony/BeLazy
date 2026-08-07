# W03 Review Round 3 — Changes Requested

## Reviewed snapshot

- Commit: `acefde0a42f2e4bf30702ee7532126f5ab69cc69`
- Tree: `1e4854debd48a77485cd0ba31fe614050529aa6d`
- Parent: `110b450fdbedf8e359784c08fd42fb52a4d97fbc`
- Verdict: `CHANGES_REQUESTED`

## Consolidated findings

- A caller could co-tamper the registry, activation, ownership, tasks, and
  assessments into a reduced internally consistent Director mesh because
  production consumers were not anchored to the exact target-owned registry.
- A self-rehashed coherent artifact could be reused as the base of a new
  promotion chain without recursively proving its antecedent, and round two
  verified only the last predecessor hop instead of recomputing the full
  blocked round-one evidence.
- Dot and empty path segments permitted aliases such as `./a`, `a/./b`, and
  `a//b` to evade immutable reference collision checks across Blueprint locks,
  Director tasks, projections, and observations.

The findings were repaired in `7c7eefad23c6841be03a966b1a1c9083bea113e7`.

