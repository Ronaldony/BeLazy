# W00 review round 2

## Reviewed subject

- Commit: `591c317630897f8efe29062ee263520a68167a36`
- Tree: `d84683e183f4960b11c33ca06c2362b8ca81ffe8`
- Tested evidence commit: `0b29525b93b8e203481fca2282b9f4393b2c4a7a`
- Tested evidence tree: `ee2c38d36c1eddb6be2b1857b453bad9a0e79a6a`

Three fresh independent read-only reviewers inspected the repaired Git subject.
No reviewer modified files, state, Git configuration, or external systems.

## Blocking verdict

| Review role | Verdict | Critical | High |
|---|---:|---:|---:|
| Architecture and contract | PASS | 0 | 0 |
| Security and authority boundary | PASS | 0 | 0 |
| Test, compatibility, and packaging | PASS | 0 | 0 |

Merged W00 blocking verdict: **PASS — Critical 0, High 0**.

## Round-one closure confirmed

- The source baseline now binds immutable bootstrap and tested Git objects.
- Report and receipt digests are verified against exact committed bytes.
- The receipt includes source inventory, handoff, import-map, and import-plan
  identities and exact argv expressed through stable local roles.
- Host-local paths were removed from tracked evidence.
- The no-follow target boundary gate covers reparse/symlink, root Git
  indirection, nested Git, generated material, and case/NFC collisions.
- The accepted bootstrap ADR specifies fresh local history and no remote.
- State correctly remained `reviewing` with a null checkpoint until this
  three-reviewer gate completed.

## Substantiated non-blocking findings and resolution

### Stale ExecPlan test count — resolved in checkpoint changeset

The ExecPlan retained the pre-review `331 passed` count. It now records the
sealed `343 passed in 19.42s` result and tested commit/tree.

### Receipt local-value mapping — resolved in ignored local state

The receipt used stable roles and stated that exact values were retained
locally. Ignored state now records the exact test-runner, verified source-copy,
and source/target basetemp values. Tracked evidence remains privacy-safe.

### State shape did not prove recovery semantics — resolved

The state schema now constrains 40-hex commits, exact W00–W07 identities and
task IDs, typed test/review records, and PASS/checkpoint invariants. A separate
read-only semantic gate compares local state with Git HEAD/ancestry, target
cleanliness, source/handoff digests, latest review roles, and unresolved
findings.

### Provenance ancestry hardening — resolved

The W00 provenance gate now requires both the bootstrap and tested evidence
commits to be ancestors of current HEAD, not merely locally available objects.

## Deferred low-risk item

The immutable handoff verifier does not reproduce every extended ZIP safety
negative used by the independent W00 preflight. The fixed archive passed all
extended checks. A target-owned hardened input verifier remains assigned to
the final audit; no broader PASS is attributed to the smaller handoff script.

## Checkpoint authorization

The W00 implementation, repairs, tests, and review gate now authorize creation
of the prescribed local checkpoint. The commit hash cannot be embedded in its
own tree; ignored local state and a subsequent tracked recovery anchor record
the existing checkpoint commit/tree without self-reference.
