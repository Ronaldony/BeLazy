# New Repository Bootstrap Specification — `be-lazy`

## 1. Decision

`be-lazy` is a new successor repository. `video-production-core` remains an independent, unchanged source baseline. No in-place repository rename, hosted slug rename, remote migration, or write-back to the source is part of this program.

## 2. Repository topology

```text
SOURCE BASELINE                           TARGET IMPLEMENTATION
video-production-core                    be-lazy
read-only                                agent-writable branch
existing history/remote retained         fresh history/new remote
behavior and test oracle                 redesigned system
```

The relationship is one-way:

```text
source observation → import plan → target changes → parity report
```

There is no target-to-source synchronization path.

## 3. Default bootstrap profile

### Profile A — Fresh history with controlled snapshot import

This is the default.

- Create an empty target Git repository named `be-lazy`.
- Record the exact source commit or archive digest.
- Import selected source files without source Git metadata.
- Preserve baseline code behavior and tests before architectural replacement.
- Add target-specific governance and provenance files.
- Make all future implementation changes only in the target.

### Profile B — History-preserving migration

Not part of the default. It may use a filtered or mirrored history only after an explicit decision covering author privacy, large files, secrets, licenses, branch/tag scope, and remote side effects. Coding agents must not choose this profile on their own.

### Profile C — Clean-room rewrite

Not the default because the existing characterization tests and validated pure-core assets are valuable. It requires a separate scope decision and a parity harness against the source release.

## 4. Source baseline forms

The agent supports either:

1. a Git checkout, preferably clean and pinned to a commit; or
2. an immutable archive with a verified SHA-256.

For the source archive supplied with the original analysis:

```text
filename: video-production-core-main (1).zip
sha256: 954325b77028bcf7d36a88136d8e1ed0ec2348ad15014623710614cced94034a
```

A different source is allowed, but its exact identity must replace this value in the target provenance record.

## 5. Source immutability requirements

The source is `READ_ONLY_REFERENCE`.

Forbidden operations include:

- file create, edit, delete, move, chmod, formatting, lockfile update;
- `git checkout`, `switch`, `reset`, `clean`, `stash`, `commit`, `rebase`, `merge`;
- worktree or submodule changes;
- dependency installation into the source tree;
- writing test caches, coverage data, build output, virtual environments, or logs into the source.

Tests against the source must redirect temporary files and caches outside the source tree. If a test tool cannot do that, run it against a verified temporary copy and record the copy digest.

## 6. Target bootstrap requirements

The target must satisfy all of the following:

- canonical repository name: `be-lazy`;
- canonical path differs from the source path;
- its Git root is independent;
- no nested source `.git` directory or inherited source remote;
- no source credential, secret, local path, cache, build output, or virtualenv;
- first commits are reviewable, reproducible, and mapped to this specification;
- only agent or controlled automation writes files in the normal path.

## 7. Import classification

Every source path receives exactly one disposition.

| Disposition | Meaning |
|---|---|
| `BASELINE_COPY` | Copy exact bytes as a compatibility baseline |
| `PORT_WITH_FIX` | Port into target and immediately apply a documented P0 fix |
| `REFERENCE_ONLY` | Do not import; retain only a reference/provenance record |
| `REGENERATE` | Recreate from authoritative source or build process |
| `DROP_GENERATED` | Exclude cache, build, test, or generated output |
| `REJECT_SECRET_OR_EXTERNAL` | Exclude secrets, credentials, machine-local or external material |

The import map records source path, target path, source digest, disposition, rationale, and verification result.

## 8. Mandatory exclusions

At minimum exclude:

```text
.git/
.github credentials or local tokens
__pycache__/
*.pyc
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage*
htmlcov/
build/
dist/
*.egg-info/
.venv/
venv/
.env*
local credential/config files
IDE state
OS metadata
runtime workspaces and generated media
```

Repository-owned CI definitions may be imported after inspection; secrets and host-side configuration may not.

## 9. Provenance artifacts in target

The agent creates:

```text
docs/provenance/source-baseline.json
docs/provenance/import-map.csv
docs/provenance/baseline-test-report.md
docs/provenance/source-license-and-notices.md
docs/architecture/be-lazy-redesign-blueprint-v3.md
docs/governance/source-baseline-policy-v1.yaml
```

`source-baseline.json` binds the source identity, import tool/version, target bootstrap commit, file counts, test summary, and license.

## 10. Public identifier policy

The repository identity is `be-lazy` from its first target commit. Existing distribution, import, CLI, schema, artifact, and serialized identifiers are initially retained only as imported compatibility contracts.

The target must not present `video-production-core` as its repository name. When the string is retained, it must be classified as one of:

```text
LEGACY_DISTRIBUTION_CONTRACT
LEGACY_IMPORT_API
LEGACY_CLI_CONTRACT
LEGACY_SCHEMA_OR_SERIALIZED_ID
SOURCE_PROVENANCE
HISTORICAL_RECORD
```

New identifiers are introduced only through a versioned migration with coexistence, deprecation, and rollback rules.

## 11. Commit and remote policy

Recommended initial target commit sequence:

1. `chore: bootstrap be-lazy governance and provenance`
2. `chore: import video-production-core source baseline`
3. `fix: repair baseline trust-boundary defects`

A coding agent may create local commits/checkpoints when allowed, but must not create the hosted repository, push, merge, or alter branch protection without separate authority.

## 12. Human direct file access

The human normal path is:

```text
create empty repository → invoke agent → review diff/report → approve merge
```

The human does not manually copy source files, rewrite package metadata, update generated schemas, or patch target code. Any exceptional direct mutation is recorded as R4 break-glass and followed by full reconciliation.

## 13. Bootstrap completion criteria

- source identity and pre/post immutability evidence exist;
- target is a separate Git root with no inherited source remote;
- all imported files have an import-map row and verified digest;
- all exclusions are recorded;
- source and target baseline test results are compared;
- target repository-facing identity is `be-lazy`;
- legacy public identifiers are explicitly classified, not blindly replaced;
- target contains AGENTS, ExecPlan guidance, architecture, governance, and rollback documentation;
- no human manual file editing is required for the bootstrap.
