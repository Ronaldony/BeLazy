# be-lazy Prompt-Only Autopilot ExecPlan

## Objective

Build the independent `be-lazy` successor repository from the immutable
`video-production-core` archive, preserve compatibility identifiers, repair the
known trust boundaries, and implement W00 through W07 with deterministic local
evidence and no external side effects.

## Inputs and boundaries

- Target: `TARGET_ROOT` (exact canonical path is retained only in ignored local state)
- Source archive: `SOURCE_ARCHIVE` / `video-production-core-main.zip` (exact canonical path is retained only in ignored local state)
- Source SHA-256: `954325b77028bcf7d36a88136d8e1ed0ec2348ad15014623710614cced94034a`
- Handoff manifest SHA-256: `1bd3aa1687299c15f81539641afcd1563097b0ff8cebc9c24a793e132386244a`
- Branch: `agent/autopilot-v4`
- Remote count at bootstrap: 0
- Network, dependency download, remote contact, publish, deploy, provider calls,
  credential use, human-approval synthesis, and public-identifier migration are
  forbidden.

## Progress

| Wave | Status | Tasks | Checkpoint |
|---|---|---|---|
| W00 Bootstrap/Baseline | in progress: implementation tested, reviews pending | BOOT-001..005 | pending |
| W01 Trust Boundary | pending | P0-PKG-001, P0-JSON-001, P0-AUTH-001 | pending |
| W02 Managed Mutation | pending | MUT-001..004 | pending |
| W03 Director/Blueprint | pending | BP-001, DIR-001, DIR-002, BP-002 | pending |
| W04 Workflow/Authority | pending | WF-001, AUTH-001, AUTH-002, WF-002 | pending |
| W05 Automation/Quality/Release | pending | SEL-001, QA-001, REL-001 | pending |
| W06 Runtime/Migration | pending | RUN-001, RUN-002, MIG-001, REL-002 | pending |
| W07 Final Audit | pending | AUDIT-001..003 | pending |

## W00 decisions and evidence

1. The observed ZIP filename lacks the advisory ` (1)` suffix, but its exact
   SHA-256 matches the mandated source identity. The digest is authoritative.
2. The empty sibling directory `BeLazy` was selected as the target because it
   is distinct from and non-nested with source and handoff.
3. All 211 archive members were classified: 178 exact copies, two ports with
   fixes, 30 recreated inner directory entries, and one reference-only archive
   wrapper. No source instruction, secret, cache, build output, virtualenv, Git
   metadata, or runtime media member exists.
4. `README.md` identifies the repository as `be-lazy`; distribution/import/CLI,
   Schema IDs, artifact versions, and serialized markers remain legacy
   compatibility contracts.
5. The repository-neutrality scanner now excludes only explicit target
   engineering control and immutable provenance paths. Product paths remain
   scanned, with positive and negative regression tests.
6. The source contains no license declaration. The program records
   `NOT_PROVIDED` and does not infer rights.
7. A pre-existing local Python 3.12 tool environment was used read-only for
   pytest/jsonschema. It is not a target dependency. No package was installed.
8. `.be-lazy/autopilot/state.json` is local control state and is excluded only
   through `.git/info/exclude`, allowing it to record the current checkpoint
   after a commit without a self-referential commit hash. Immutable Wave
   receipts, provenance, reports, and this ExecPlan remain tracked.

## W00 actual checks

- Input verifier: PASS; handoff 49/49 and source 211 members.
- Source temporary-copy pytest: 329 passed.
- Source purity/side-effect/isolation tools: PASS.
- Initial target parity run: 327 passed, 2 failed due the documented control
  plane purity scope collision.
- Focused repair tests: 15 passed; core purity PASS.
- Final target full suite: 331 passed.
- Target side-effect-free and repository-isolation checks: PASS.
- Source and handoff post-check: exact digests unchanged.

## Open non-blocking gaps

- No source license or license metadata was supplied.
- The host lacks an offline standard build frontend, setuptools, and wheel.
  The original source's trusted audit already demonstrates the known
  zero-Schema wheel defect; W01 owns the packaging repair and offline
  verification strategy.

## Recovery

W00 began from an unborn branch. Until the first local commit exists, recovery
must reconstruct the target from the verified archive and handoff using the
recorded import map. After W00 checkpointing, local state will record the exact
current commit while tracked receipts retain the immutable Wave evidence.
