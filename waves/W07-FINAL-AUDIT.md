# W07 — Final independent audit and delivery

## Task IDs

`AUDIT-001`, `AUDIT-002`, `AUDIT-003`

## Objective

Prove the repository is internally coherent, installable, testable and migration-ready, then produce the final machine-readable and human-readable delivery evidence without external side effects.

## Required audit

- Re-read the original source assessment and every mandatory v4 completion condition.
- Run focused tests for all new contracts and negative boundaries.
- Run the full test suite in the available supported environment.
- Run purity, side-effect and repository isolation checks.
- Build wheel/sdist as applicable, inspect contents and test isolated install/schema loading.
- Verify registered artifact/schema versions, migration registry and projection coverage.
- Run legacy/new workflow parity corpus.
- Scan for secrets, caches, build leftovers, unsafe symlinks, path collisions, untracked files and unexpected instruction/config files.
- Recompute source and handoff fingerprints.
- Spawn at least four fresh read-only final reviewers: architecture, security/authority, test/packaging and documentation/migration.
- Repair and repeat until zero Critical/High findings.

## Required outputs

- final state
- final result JSON validated against its schema
- final report
- complete test/command receipt
- source provenance/import map
- compatibility and deferred T90 decision
- rollback instructions and commit range

## Final repository state

- clean working tree
- any preconfigured remote unchanged and not contacted
- local completion checkpoint and optional local tag
- no network, publish, deploy, provider or human-approval synthesis side effects
