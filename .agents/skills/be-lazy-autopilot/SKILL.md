---
name: be-lazy-autopilot
description: Execute the complete be-lazy successor-repository redesign from an immutable video-production-core source using one prompt, autonomous Wave progression, read-only subagent reviews, automatic repair, local checkpoints, and no human file editing.
---

# be-lazy Autopilot Skill

Use this skill when the user authorizes the full local `be-lazy` successor-repository program.

## Start

1. Verify source and handoff exact digests and path separation.
2. Read the target `AGENTS.md`, `AUTOPILOT_PROGRAM.yaml`, autonomous decision defaults, completion contract, Wave spec, and stable references.
3. Create or resume `.be-lazy/autopilot/state.json` and the living ExecPlan.
4. Work through W00–W07 without human checkpoints.

## Wave loop

For each first incomplete Wave:

1. record starting HEAD/tree/source/handoff fingerprints;
2. spawn at least two fresh read-only discovery subagents;
3. integrate their evidence and implement in the main Supervisor thread;
4. run focused then regression tests;
5. spawn three fresh read-only reviewers: architecture, security/authority, tests/compatibility;
6. merge findings and automatically repair Critical/High findings;
7. rerun with fresh reviewers until PASS or repair policy exhaustion;
8. update state, provenance, receipts, and ExecPlan;
9. create a local checkpoint commit and immediately continue.

The main Supervisor is the only writer. Do not use parallel write-heavy agents.

## No questions

Resolve ambiguity with `AUTONOMOUS_DECISION_DEFAULTS.yaml`. Do not ask the human to choose implementation details or modify files. Stop only on an enumerated hard blocker.

## Authority boundary

The initial prompt authorizes local repository engineering only. It does not create application human approval. Never synthesize approval identity, signature, ledger record, publish grant, or provider credential.

## End

Run W07 final audit. Generate final state/report/result, ensure the working tree is clean, and return the factual completion summary.

Read the reference files in this skill directory for the detailed loop, review protocol, and recovery rules.
