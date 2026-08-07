# W00 review round 1

## Scope and verdict

Three independent read-only reviewers inspected commit
`b7aaaf2ac43ca99970c317a7d505316a07d320e9` (tree
`0f9f3cf065261a0b0f24faea72fa116687bb59d2`). The reviewed worktree was not
mutated.

- Architecture and contract reviewer: Request Changes
- Security and authority-boundary reviewer: Request Changes
- Test, compatibility, and packaging reviewer: Request Changes
- Merged severity: Critical 0, High 2, Medium 4, Low 1
- Wave verdict: not passed

## Merged findings and dispositions

### High — baseline provenance and tests were not Git-bound

The tracked source baseline left its target commit fields null and did not bind
the baseline report or test receipt. All three reviewers independently found
this defect.

Resolution in the W00 repair changeset:

- bind the initial immutable commit and Git tree;
- add exact SHA-256 bindings for the baseline report and W00 receipt;
- include source and target test summaries without claiming the unexecuted
  wheel build;
- add a regression test for null/wrong snapshot and evidence bindings;
- add a read-only gate that resolves the recorded objects against the local Git
  object database.

The final tested repair commit/tree is added in a subsequent evidence-only seal
after that commit exists, avoiding self-reference.

### High — no reviewed W00 PASS checkpoint or recovery anchor existed

The only commit was an implementation snapshot. Local state correctly said
`reviewing`; the ExecPlan correctly said reviews and checkpoint were pending.
It was therefore not mislabeled as a PASS, but it could not satisfy W00
completion.

Resolution sequence:

1. commit and test the repair changes;
2. bind the tested repair commit/tree in tracked evidence;
3. obtain a fresh three-reviewer round with zero Critical/High findings;
4. update the ExecPlan and local eight-wave state to passed;
5. create the prescribed W00 checkpoint commit;
6. record a tracked recovery anchor for that existing checkpoint and a semantic
   state snapshot digest.

### Medium — tracked host-local paths

Tracked evidence exposed the host user name and canonical workspace paths.
Tracked records now use logical roles, basenames, and SHA-256 path fingerprints;
exact paths remain only in ignored local state. A regression test rejects a
host-local absolute path or user marker in the affected tracked records.

### Medium — control-plane reparse boundary gap

Product purity intentionally excludes engineering controls, so it cannot also
serve as a repository containment check. A separate no-follow target-boundary
gate now rejects root Git indirection, nested Git metadata, symlink/reparse
entries, non-regular paths, generated/cache material, and case/NFC collisions.
Synthetic negative tests cover the fail-closed rules.

### Medium — receipt commands were not exact and counts were stale

The final receipt is regenerated from the repaired target with interpreter
identity and digest, cwd/path-role mapping, safety environment, complete argv,
external basetemp definition, and actual results. Placeholder commands and
pre-repair scan counts are not retained as final evidence.

### Medium — bootstrap ADR remote ambiguity

ADR-BOOT-001 is accepted and now states fresh local history with no remote.
Hosted remote creation or configuration remains outside this mandate.

### Low — handoff verifier hardening

The supplied handoff verifier does not independently reproduce every extended
archive-safety check used in preflight. The immutable input was independently
verified safe in W00. A target-owned reproducible verifier remains a low-priority
audit hardening item for the final program audit; no unsupported PASS is
inferred from the smaller handoff script.

## Positive evidence retained

- source and handoff digests independently matched and remained unchanged;
- 211 of 211 archive members have one disposition;
- 178 exact-copy files and two documented target ports matched their recorded
  digests;
- no source Git metadata, remote, secret, cache, build output, or reparse entry
  was present;
- legacy public identifiers remained compatibility contracts;
- the unavailable wheel build was reported as an environmental gap, not PASS.
