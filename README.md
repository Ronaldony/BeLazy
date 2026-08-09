# be-lazy

`be-lazy` is the independent successor repository to the immutable
`video-production-core` baseline. During the compatibility phase it retains
the legacy Python distribution (`video-production-core`), import namespace
(`video_factory`), CLI (`video-factory`), Schema IDs, artifact versions, and
serialized protocol markers. Those public identifiers are compatibility
contracts, not the repository identity, and their migration is deferred.

Source provenance and the controlled import disposition are recorded under
`docs/provenance/`. This repository does not inherit source Git metadata or
remotes.

core_contract: 0.3
rules_version: supplied by the consuming workspace

Channel-neutral, deterministic contracts for AI video-production workflows.
The core validates and plans; it does not execute paid generation, write
production artifacts, approve gates, or publish content.

## Declarative workflow and authority boundary

W04 adds a target-owned versioned claims/gates/actions DAG and a separate
authority control plane. Workflow evaluations expose every blocker, parallel
action frontier, and recommended next action, but evaluations, executable
plans, parity reports, legacy approvals/readiness, Director results, and
Blueprints all have `authority_effect=none`.

Execution authority is an exact `ActionAuthorityRequest` plus a current
ledger-verified `AuthorityDecision`. Dispatch, reconcile, and managed mutation
obtain a fresh purpose-bound verification/reservation receipt immediately
before a side effect. The runtime scope passed to the executor must exactly
match the authorized subject, adapter/provider, model, destination, limits,
and retry facts. The requester cannot self-approve; each human is bound to a
distinct signature verification, and fresh revalidation cannot replace the
initial authority basis. Assurance level, autonomy level, and action risk are
independent; mode names or AI consensus never replace authority. Raw standing
grants are unverified inputs, approval requests cannot create authority, and R4
forbids standing grants. Managed mutation remains fixed R4 and requires both
the W04 dual-human decision and its existing exact break-glass evidence.

The repository packages the workflow, authority policy, parity normalization,
73 schemas (68 registered artifact versions), and their exact manifests in the
offline wheel. Concrete persistent ledger, executor, and crash-recovery
implementations remain deferred to the runtime wave.

## 0.3 continuity

- `generation-packet/2.1` lets a shot declare what its first frame inherits
  (`continuity.carried_elements`) and what that frame depicts
  (`first_frame.depicted_elements`). The new `first_frame_state_carryover`
  check derives the complete expected set from the preceding hash-bound
  storyboard shot's `end_state_elements`, then proves the source shot precedes
  the inheriting shot and every required element is declared and depicted.
  Missing storyboard evidence is inconclusive; a declared empty set is the only
  way an empty carryover array passes. `2.0` packets are still accepted and emit
  no such check.
- `continuity-qc/1.0` compares one opaque element **between two generated
  clips** on relative-scale, orientation/shape, and presence axes. A single-shot
  QC report cannot express that relation. The report preserves its closed
  measurement evidence and all policy inputs, so
  `rejudge_continuity_qc()` can reproduce the verdict from serialized data.
- Multi-shot orchestration requires one current pass/warn continuity report
  whose subjects exactly hash-bind every packet-shot output and whose
  comparisons cover every adjacent last-to-first shot pair on all three axes.
- `continuity_anchor` proves only that an anchor was **declared**. Whether a
  generator actually preserved that anchor is not knowable before generation;
  it is decided afterwards by `continuity-qc/1.0`.
- Tolerances remain owner data. Planning a numeric axis without a
  caller-supplied finite, nonnegative tolerance is an error, never a default.

## QC severity semantics

Single-shot QC and continuity QC use the same severity-to-judgment mapping:

| Severity | Failed constraint |
|---|---|
| INFO | Records the finding but returns `PASS`; it does not lower the overall judgment |
| WARNING | Returns `WARN` |
| ERROR | Returns `FAIL` |

For INFO, `Finding.passed = false` still records that the constraint expression
was not satisfied. `PASS` means only that informational evidence does not lower
the severity-adjusted judgment.

## 0.2 hardening

- Validated `ArtifactSnapshot` graphs replace presence-only orchestration.
  Current artifacts must share episode/rules provenance, and every review or
  approval is checked against exact path+SHA-256+artifact-version bindings.
- `generation-packet/2.0` declares the facts needed for pre-generation
  feasibility: capability, minimum duration, source-frame dimensions and
  before-state, continuity/master-plate requirements, and render dependencies.
- Pending `approval-requirement/1.0` documents are distinct from granted
  human evidence. Packet-local boolean flags never authorize generation.
- Shot QC distinguishes `warn`, `fail`, `inconclusive`, and
  `not_applicable`, with caller-supplied fallback measurement methods.
- Final encoding can mux one video with an optional separate soundtrack and
  carries post-encode checks and fallback profile references as data.
- Pure media matching recognizes accidental `.mp4.mp4` output with a warning
  while enforcing caller-injected aspect dimensions.
- Final delivery, independent final review, publish metadata, and publish
  approval are explicit evidence gates. Core still leaves the external publish
  action to a human/channel tool.

## Boundary

The repository must not contain channel or character identities, creative
pillars, provider-specific limits, episode identifiers, local user paths,
credentials, or concrete adapter bindings. Such values are supplied by
workspace rules, profiles, policies, adapters, and observed artifacts.

All `src/` APIs remain plan-only:

- no filesystem writes, deletes, or moves;
- no child-process launch;
- no network access;
- no automatic approval, generation, selection, or publish action.

## Python and installation

Python 3.12 is supported. Install a verified local wheel with networking
disabled:

```powershell
python -m pip install --no-index --find-links "<approved-wheelhouse>" "video-production-core==0.3.1"
```

A channel runtime should consume a hash-verified wheel rather than an editable
source checkout.

## Development checks

Provide dependencies from an approved offline wheelhouse, then run:

```powershell
python -m pytest
python tools/check_core_purity.py
python tools/check_side_effect_free.py
python tools/check_repo_isolation.py
```

No command in this repository downloads dependencies. Tests use synthetic
fixtures and do not read a channel workspace.

See `CONTRACTS.md` for the public API and artifact contracts.
