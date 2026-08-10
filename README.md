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
quality/release policy, W06 fixture-runtime policy, 87 schemas (82 registered
artifact versions), and their exact manifests in the offline wheel. Production
ledger, credential, provider, publisher, and migration implementations remain
disabled.

## Automated selection, quality, and release boundary

W05 adds a non-side-effect automation plane. A QualityBundle covers nine
independent dimensions for exact current media and binds an independently
supplied origin time; every evaluator receipt is verified at origin and again
at the current consumer time. Hard, safety, continuity, and platform failures
cannot be hidden by averages. CandidateDecision selects
automatically only with current media/evaluator evidence, a trusted confidence
receipt for every candidate, a unique winner at all score/confidence/margin
thresholds, exact workspace/channel/concept/episode scope, an independently
supplied immutable origin evaluation time, and both original and current W04
`auto_select_candidates` trusted-ledger authority.
Otherwise it records a stable escalation or denial reason. Complete rejected
authority request/risk/decision/receipt evidence remains bound as
non-authorizing audit provenance, while
partial evidence/ledger input is rejected outright.

ReleaseCandidate binds final media, metadata, subtitle/accessibility,
thumbnail, integrated quality, selection provenance, destination, context, and
workspace observation and all four production-scope IDs without creating an
authority digest cycle. Its original creation time is supplied independently
during verification. A later ReleaseAssessment first reverifies that exact
candidate at its assessment time
and requires exactly one current one-shot human release approval. It can make
the package eligible for handoff, but never publishes:
there is no publisher API in the W05 core plane and every W05 artifact has
`authority_effect=none`.
Legacy ranking/edit artifacts remain unchanged and their projection is
read-only and non-current.

## Durable fixture runtime and migration boundary

W06 adds pure durable artifacts under `video_factory.runtime` and concrete
local adapters in the separate `video_factory_runtime` package. The latter can
open only a marker-bound isolated fixture root and packages
`production_enabled=false`. It contains a SQLite atomic-idempotency journal,
stable no-follow workspace access, managed mutation, a synthetic external
executor, a fake publisher, and reversible read-only projection migration. It
does not contain a real provider, real publisher, credential material, network
client, or production filesystem adapter.

Every new effect is preceded by exact workspace, identity, kill-switch, W04
purpose-bound reservation, destination, and idempotency checks. Journal states
after a dispatch marker are reconcile-only; they are never blindly retried.
The canonical intent includes the exact ordered mutation-effect sequence, and
the atomic dispatch claim revalidates the execution row and full event chain.
Exact terminal replay only returns the existing durable receipt. Publication
requires a separate R3 side-effect action, current release/workspace proof, and
a broker-issued non-serializable credential lease; W05 `ready` alone cannot
publish.

Migration supports only `legacy_only`, `dual_read_compare`,
`projection_read_only`, and `rolled_back` in fixture tests. Projection selection
requires exact pinned parity and a separate activation record, stays read-only
and non-authorizing, and never marks the projection current. All production
consumer entries remain unregistered, so public cutover is still deferred.

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

All APIs under the pure `src/video_factory` package remain plan-only:

- no filesystem writes, deletes, or moves;
- no child-process launch;
- no network access;
- no automatic approval, generation, selection, or publish action.

The separate `src/video_factory_runtime` package is covered by
`tools/check_runtime_boundary.py` and is restricted to marker-bound synthetic
fixtures. It must never import a network/process launcher or accept a real
production root.

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
python tools/check_runtime_boundary.py
python tools/check_repo_isolation.py
```

No command in this repository downloads dependencies. Tests use synthetic
fixtures and do not read a channel workspace.

See `CONTRACTS.md` for the public API and artifact contracts.
