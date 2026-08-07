# W00 — Bootstrap and verified baseline import

## Task IDs

`BOOT-001`, `BOOT-002`, `BOOT-003`, `BOOT-004`, `BOOT-005`

## Objective

Create a new independent `be-lazy` Git repository, establish trusted Agent governance and state, inventory every source archive member, import an approved snapshot without source instructions or generated material becoming active, and prove the baseline behavior.

## Required work

- Verify canonical source/target/handoff separation and input digests.
- Initialize target Git only if it is empty; create `agent/autopilot-v4`.
- Install trusted `AGENTS.md`, Skill, read-only custom agents, state, ExecPlan and provenance skeleton.
- Inventory every source archive member with one disposition: `BASELINE_COPY`, `PORT_WITH_FIX`, `REFERENCE_ONLY`, `REGENERATE`, `DROP_GENERATED`, or `REJECT_SECRET_OR_EXTERNAL`.
- Reject `.git`, remotes, credentials, cache, build/dist, egg-info, virtualenv, runtime media, local configuration and source-side instruction activation.
- Import baseline source, tests, schemas, docs and packaging according to the map.
- Preserve initial Python distribution, import namespace, CLI, Schema `$id`, artifact versions and serialized identifiers.
- Run available baseline tests and record source/target differences without hiding failures.
- Record exact source archive member inventory, license, package, schema and test statistics.

## Acceptance

- Source archive pre/post SHA-256 matches the expected digest.
- Every source member has exactly one disposition.
- Target has a fresh local branch/history; any preconfigured remote is recorded, unchanged and never contacted.
- Imported source instruction files are inert reference data or excluded.
- Baseline tests are reproduced and classified.
- Target contains no cache, secret, generated runtime artifact or source `.git` material.
