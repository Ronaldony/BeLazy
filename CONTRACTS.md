# Public contracts

rules_version: supplied by the consuming workspace
core_contract: 0.3
config_contract: 1.0

The public surface is the set of names re-exported by each package `__init__.py`. Internal helpers and repository
tools are not public runtime API.

## Core 0.3 gate model

Production planning is evidence-based, not presence-based:

1. The caller observes exact artifact bytes and supplies path/hash/document
   snapshots. Raw documents are rejected by orchestration.
2. Core validates every registered schema and requires one explicitly current
   artifact for singleton families. Mixed episode or rules provenance blocks.
3. Reviews must pass, bind to the current subject hash, and satisfy distinct
   reviewer counts from workflow policy.
4. Generation requires `generation-packet/2.0` or `2.1`, the version-specific
   complete independent feasibility pass, and exact human approval evidence in
   Standard/Controlled. Those facts establish structural readiness only.
   Execution additionally requires a current W04 `AuthorityDecision`, a fresh
   purpose-bound trusted-ledger receipt, and a complete trusted
   `WorkspaceObservation` bound to the same revision and manifest.
5. Every packet shot needs one current QC verdict of pass or warn. Failed or
   inconclusive QC cannot advance.
6. Multi-shot packets additionally need one current pass/warn continuity QC
   exactly hash-bound to every shot output and covering each adjacent
   last-to-first pair on all three continuity axes.
7. Candidate ranking, human edit selection, rough cut, final delivery, final
   review, metadata, and publish approval form the remaining lineage. Core
   returns `ready_for_human_publish`; it never publishes.

| Package | Public contract |
|---|---|
| `video_factory.domain` | opaque identifiers, hashes, `ArtifactReference`, validation, canonical serialization, and migration ports |
| `video_factory.brand` | closed character/location entity envelope, role-preserving fixed sentences, deterministic Markdown projection, structured round-trip evidence, and exact-source catalog port |
| `video_factory.config` | four persisted config models, seven ordered merge layers, closed validation, extension registration, provenance, and effective snapshots |
| `video_factory.engine` | workflow/execution modes, validated `ArtifactSnapshot` graph, hash-bound generation readiness, and deterministic plan-only orchestration; never transitions |
| `video_factory.policy` | Rapid/Standard/Controlled workflow policy data, mode resolution (no silent default), and ADR-004 bridge helpers |
| `video_factory.providers` | capability registry, shared descriptor/envelopes, injected constraint profiles, separate media-provider/task-executor ports, and managed-mutation authority/authenticator/executor Protocols with an immediate pre-side-effect guard |
| `video_factory.approvals` | pending requirements and granted evidence as distinct contracts; deterministic requirement IDs and exact evidence binding |
| `video_factory.workflow` | target-owned versioned claims/gates/actions DAG, complete blocker and parallel-frontier evaluation, incremental invalidation, per-action executable plan identities, and non-authorizing legacy parity reports |
| `video_factory.authority` | separate assurance/autonomy/risk contracts, exact action requests, parse-only standing grants, non-authorizing approval requests, target policy classification, authority decisions, and trusted ledger verification/revalidation ports |
| `video_factory.quality` | exact-media nine-dimension QualityBundle aggregation, target-owned hard gates, current media/evaluator-receipt verification ports, and bounded affected-target-only remediation plans |
| `video_factory.selection` | trusted-confidence/score/margin-bound CandidateDecision with exact `auto_select_candidates` W04 authority and a read-only non-current legacy ranking projection |
| `video_factory.release` | non-secret destination binding, cycle-free ReleaseCandidate identity, and exactly-one-human non-publishing ReleaseAssessment handoff |
| `video_factory.runtime` | pure W06 execution/publication/parity/migration artifacts, strict serialization, and packaged fixture-only policy projections; never performs an effect |
| `video_factory_runtime` | marker-bound fixture adapters: SQLite journal, no-follow workspace/content access, managed mutation, external executor, fake publication, and reversible read-only migration; production activation is forbidden |
| `video_factory.feasibility` | pure checks for capability, minimum duration, first-frame aspect/before-state, continuity anchors, cross-shot first-frame state carryover, and unsupported render dependencies |
| `video_factory.qc` | injected constraints with pass/warn/fail/inconclusive/not-applicable outcomes and fallback measurement hints |
| `video_factory.continuity` | cross-shot comparison of one opaque element between two generated clips (relative-scale / orientation-shape / presence); plan-only, caller-supplied finite nonnegative tolerances, closed measurement serialization, and pure serialized-document rejudgment |
| `video_factory.media` | pure observed-output matching, double-extension warning recovery, and caller-injected aspect validation |
| `video_factory.mutation` | six strict managed-mutation artifacts, deterministic exact-before/CAS planner, canonical semantic diff, immutable revision/tombstone derivation, drift quarantine, and typed serialization; never writes |
| `video_factory.blueprint` | immutable channel/concept constitutions, EpisodeIntent, detailed ProductionBlueprint with exact field ownership/provenance, strict typed JSON boundaries, and read-only non-authoritative shadow projections |
| `video_factory.directors` | target-owned 12-core/6-conditional Director registry, deterministic activation and parallel task plans, hash-bound assessments, two-round conflict synthesis, and a runtime Protocol only |
| `video_factory.storage` | no-overwrite artifact store ports, frozen-index guards, **planning-only and non-authorizing** workspace init/export; source trees containing links, reparse points, or special nodes are rejected |
| `video_factory.review` | creator/reviewer-separated request, result, revision policy, and review port |
| `video_factory.security` | path guard, secret reference, purity-scanner port, and in-process `scan_repository` / `RepositoryPurityScanner` |
| `video_factory.artifacts` | artifact schema registry (`artifact_version` → schema), single-document and batch JSON Schema validation runner, structured field-path errors |
| `video_factory.lint` | injected `VerbatimRule` / `SourceLockRule` data, JSON rule-file loader, `run_lint` → `LintReport` reusing `qc.Finding` (byte/sha256 equality + source drift); **no channel text in core** |
| `video_factory.sheets` | deterministic human generation sheet; always preview-only and never an execution-authority surface |
| `video_factory.encode` | concat/re-encode/mux command plans, explicit input roles, optional soundtrack, post-checks/fallbacks; never executes |
| `video_factory.analytics` | injected `AnalyticsRecord` snapshots, `VerificationRule`/`RetroPolicy` data, deterministic `compute_retro` → `RetroReport` (`supported`/`refuted`/`inconclusive`); **no external analytics APIs**; CLI analytics commands not wired yet |
| `video_factory.distribution` | ADR-001 `core.lock` document types, deterministic `build_core_lock` (string only), `parse_core_lock`, observation-only `verify_lock_against_installed`, and `plan_wheel_build` (argv/string only; **never builds or installs**) |
| `video_factory.cli` | command registry (`CommandSpec`); plan-only implemented surface for `doctor`/`validate`/`init`/`export`/`qc`/`approve`/`run`/`status`/`new-*`/`review`; contract-only recovery (`retry`/`resume`/`invalidate`/`reopen`); sole `not_yet_backed`: `migrate`; no silent workflow-mode default (OD-004) |

## Plan-only core invariant (2026-07-23)

Core **must not perform side effects**. Inside `src/video_factory`, allowed
operations are observe (read / existence checks), validate, compute, and
**produce structured Plan or evidence objects**. File write/delete/move,
archive creation, child-process launch, network access, and real effect ports
are forbidden there. Concrete fixture effects live only in the separate
`src/video_factory_runtime` package behind explicit contracts; humans submit
intent, review semantic diffs, and approve or deny authority, but do not
normally edit managed files directly.

This generalizes the existing `EncodeCommandPlan` pattern (`executed=False` always) and provider
`human_only` → `AWAITING_HUMAN` outcomes.

| Planner | Plan type | READY means | Rejected statuses (examples) |
|---|---|---|---|
| `plan_materialize` | `WorkspaceInitPlan` | planning-only copy operation list; never authorization-ready | `rejected_target_nonempty`, `rejected_schema`, `rejected_frozen_index`, `rejected_unsafe_tree` |
| `plan_export` | `WorkspaceExportPlan` | planning-only export file list after sensitive scan; never authorization-ready | `rejected_sensitive`, `rejected_target_exists`, `rejected_frozen_index`, `rejected_unsafe_tree` |
| `plan_mutation` | `MutationPlan` | exact request/revision/manifest/policy CAS, stable operation IDs, and structured semantic diff | stale revision/manifest/bytes, path alias/link/reparse, destination exists, untrusted/incomplete observation |
| `build_encode_command` | `EncodeCommandPlan` | argv + command string | `rejected_output_exists`, `rejected_invalid` |
| `build_qc_plan` / `judge_measurements` | `QCPlan` / `QCJudgment` | primary/fallback method hints and pass/warn/fail/inconclusive/not-applicable judgment | empty plan; unknown comparison |
| `build_approval_requirement` | `ApprovalRequirement` | path+sha256-bound requirement document | empty artifacts; invalid sha256 |
| `observe_episode_state` / `plan_next_step` | `EpisodeStateObservation` / `NextStepPlan` | validated current snapshot graph and deterministic next action | raw docs, mixed provenance, ambiguous current artifacts, missing mode |
| `build_generation_readiness` | `GenerationReadinessPlan` | `ready` preserves structural legacy planning and diagnostics; `authorization_ready` is always false in W04 and a separate authority decision is required | stale/missing/expired evidence, context, or workspace trust; Rapid mode |
| `build_declarative_gate_run` / `evaluate_declarative_gate_run` / `build_executable_production_plan` | `DeclarativeGateRun` / `WorkflowEvaluation` / `ExecutableProductionPlan` | only mode-material gate helpers run; sealed input digests drive transitive invalidation, every blocker/frontier item is preserved, and each action gets its own material-context/plan digest; `authority_effect=none` | unknown/cyclic definition, missing gate, stale context, invalid incremental reuse |
| `evaluate_authority` / `revalidate_authority_for_side_effect` | `ActionRiskAssessment` / `AuthorityDecision` / `AuthorityVerificationReceipt` | target policy recomputes risk and a trusted ledger proves current scope, signature/ledger state, revocation, limits, identity, and purpose immediately before a side effect | unknown action, stale context, insufficient source, revoked/expired grant, kill switch, budget/idempotency mismatch |
| `build_quality_bundle` / `verify_quality_bundle` / `plan_targeted_remediation` | `QualityBundle` / `RemediationPlan` | exact nine-dimension coverage, independently supplied origin time, every evaluator receipt at origin and current verification, current media, hard-failure dominance, and only affected targets within bounded lineage | missing/duplicate/stale evaluation, rebound origin time, hard/safety failure, replay, no progress, regression, oscillation, exhausted retries |
| `build_candidate_decision` / `verify_candidate_decision` | `CandidateDecision` | unique winner meets score/confidence/margin thresholds, every confidence receipt is current, exact four-ID scope matches, and original plus current W04 authority are independently verified; complete but invalid presented request/risk/decision/receipt evidence remains non-authorizing while its exact provenance is bound | tie, low score/confidence/margin, future/stale quality, stale confidence, scope mismatch, missing or rebound authority; partial authority/ledger input is rejected |
| `build_release_candidate` / `verify_release_candidate` / `assess_release_candidate` | `ReleaseCandidate` / `ReleaseAssessment` | exact release constituents, four-ID scope, and independently supplied creation time are freshly reverified; one current human approval makes the handoff eligible; never publishes | stale constituent/context/destination, scope mismatch, invalid causal time, failed quality, unverified selection, missing or invalid human authority |
| `draft_*_config` | validated config mapping | schema-valid channel/concept/episode draft | invalid scope id / settings |
| `build_core_lock` | TOML `str` | deterministic lock document text (caller writes file) | invalid artifact / path escape |
| `plan_wheel_build` | `WheelBuildPlan` | `python -m build --wheel` argv + vendor placement notes | invalid version |
| `verify_lock_against_installed` | `LockVerdict` | compatible / upgrade_available / breaking / python_mismatch | invalid lock |

Gate: `tools/check_side_effect_free.py` statically scans `src/video_factory`
(AST-first, regex backup) and exits 1 on write/process APIs.
`tools/check_runtime_boundary.py` separately forbids core imports of the
concrete runtime and forbids network/process/module-launch effects in the
fixture runtime. These complement `check_core_purity.py` and
`check_repo_isolation.py`.

## ProductionBlueprint and Director shadow plane (W03)

`ProductionBlueprint` is the detailed design source of truth. Its identity
includes eight material context digests, every canonical active field, exact
owner/verifier coverage, Director provenance, status, and blockers. Required
root and per-shot detail is validated in Python in addition to the closed JSON
Schema contract. `coherent` requires non-empty path-specific values, canonical
non-conflicting reference locks, and provenance that exactly covers every
active owner and verifier. The general Blueprint builder cannot mint this
state. Director synthesis derives it from the validated evidence set, and a
caller relying on a persisted promotion must recompute that exact source,
activation, task, assessment, synthesis, base, and context binding. Task
planning and synthesis additionally require the actual validated
Channel -> Concept -> EpisodeIntent source bundle and exact episode identity;
opaque context digests alone are insufficient.

Director work is logically parallel. Every task and assessment binds the exact
base Blueprint, context, registry, activation policy, charter, immutable input
references, model/prompt identity, request/response digests, and execution
receipt. Production mesh entry points accept only the exact target-owned
12-core/6-conditional registry and an unevaluated `draft` base; a loaded
`coherent` artifact cannot begin another promotion chain without a future
recursive evidence contract. Conditional activation and policy digests are
re-derived at every consumer, and unknown signals fail closed. Round two also
recomputes the complete round-one task and assessment evidence before accepting
the blocked predecessor. Any blocker, uncertain result, low
confidence, stale patch, incomplete
coverage, or unresolved conflict blocks synthesis. Conflict resolution has a
hard maximum of two rounds backed by a deterministic conflict-session ID and an
exact blocked-predecessor digest for round two.

`blueprint-projection/1.0` is the only serialized projection family. Brief,
storyboard, generation, edit, sound, and publish are nested view kinds, not
top-level legacy artifacts. Projections are always shadow-only, read-only,
non-editable, and have `authority_effect=none`; supplying one as current input
adds a blocking ArtifactGraph finding. Neither a coherent Blueprint, a Director
PASS, nor a shadow diagnostic grants readiness or execution authority. W03 has
no semantic legacy-to-Blueprint normalizer. It records caller-asserted fields as
an explicit `unverified`, `diagnostic_only` observation bound to exact legacy
bytes. Equality means only that those asserted fields equal the projection; it
is not parity, migration, cutover, readiness, or authorization evidence.
`ArtifactReference.sha256` always means exact serialized bytes; logical
Blueprint/projection identity digests remain separate fields.
All W03 immutable references share one canonical relative POSIX/NFC path rule:
empty, `.`, `..`, repeated-separator, trailing-separator, absolute, drive,
backslash, control, case and Unicode aliases fail closed.

## Declarative workflow and authority control (W04)

`video_factory.workflow` adds the exact target-owned
`episode-production-workflow/1.0` definition with 24 claims/gates and 26 legacy
action identities. Evaluation is pure and complete: it returns satisfied
claims, every blocker, all currently executable actions, the stable recommended
action, consumed evidence, and the six material context inputs. Packet review
and feasibility, and final review and metadata preparation, can appear together
on the frontier. Each gate binds only the material-context fields it declares;
a stale PASS is rejected, while an unrelated context change invalidates only
the transitive dependent claims. The target adapter fingerprints only inputs
read by the selected mode and `evaluate_declarative_gate_run` binds those
sealed input digests into the evaluation. Rapid therefore reuses review and
approval gates across irrelevant time/evidence changes; Standard and
Controlled invalidate the current approval gates and their dependants when
evaluation time or bound approval evidence changes. Its semantic projection must equal a clean
full recomputation. The incremental adapter skips unchanged gate helpers,
binds the previous evaluation SHA, and requires the complete predecessor chain
and exact target-claim invalidated/reused partition before reuse metadata can
be accepted. Plan construction, parity comparison, and workflow authority
requests receive that same ordered oldest-to-newest evidence bundle and repeat
the target-DAG verification. At most eight predecessors may be carried; the
next update must materialize a predecessor-free clean evaluation.

Routine storyboard and packet assessment is a non-human integrated preflight;
the compatibility storyboard-approval gate is satisfied without consuming
human evidence. A material creative deviation is handled as R4 action
authority, not as a restored routine storyboard checkpoint.

Each frontier action produces a distinct `executable-production-plan/1.0` and
full seven-digest `GateContext`, including that action's plan digest. A
`WorkflowEvaluation`, executable plan, legacy `NextStepPlan`, parity report,
Director result, Blueprint, mode name, or AI consensus has
`authority_effect=none`. The legacy planner remains available during dual-run.
The characterization corpus contains 26 fixed seed states evaluated in Rapid,
Standard, and Controlled (78 rows). Rapid intentionally maps production-ready
seeds to preview-only; a committed expected-action matrix records this rather
than pretending every action is reachable in every mode. The legacy matrix
covers all 26 identities; the target matrix intentionally omits the dormant
`approve_storyboard` recommendation and records its exact consolidation into
`create_generation_packet`. The corpus compares action, blockers, actor, required
authority, consumed evidence, and prohibited actions. Loaded evaluations are
cleanly recomputed from their gate results under the exact target DAG before
comparison or plan construction. A parity report cannot cut over or authorize,
and any unexplained dimension fails. The 78 rows feed the same episode
observation to the legacy planner and an independent declarative gate adapter;
no expected action is fed into that adapter. Explanation codes are
dimension-scoped and validate target-owned blocker codes or the committed
action-row-specific blocker and legacy-label-to-target-claim/SHA evidence
shape. The process-consolidation explanation is likewise restricted to the
single exact storyboard transition across action, blocker, actor, authority,
and evidence dimensions. Unmapped blockers and labels borrowed from another valid row fail.

`video_factory.authority` keeps three independent axes: `AssuranceProfile`
describes evidence rigor, `AutonomyProfile` describes how work may be proposed,
and `ActionRisk` determines authority. An `ActionAuthorityRequest` binds the
exact request-envelope/idempotency identity and requester principal, workflow
action, exact target-verified workflow evaluation plus its ordered predecessor
digests and objects, and executable plan, all seven
current-context digests, workspace/channel/concept/episode,
provider/model/destination, canonical artifact and output scopes, cost/currency,
candidate/retry limits, requested profiles, and the closed target
hard-escalation catalog with exact evidence. The target-owned policy bundle
recomputes risk; an unknown action is unsupported and fails closed. Its
resource also binds the exact governance YAML digest, hard-escalation trigger
catalog, self-approval prohibition, and disabled W04 release-campaign state.
Known triggered facts raise the effective risk and authority floor to R4 plus
two independent humans; UNKNOWN facts deny. Omitted, reordered, or
evidence-free facts are invalid. Every enforcement-matrix row names its own
executable positive and negative test node, owner, phase, and observed reason.

`standing-authorization/1.0` is parse-only unverified input. Core exposes no
grant/sign/issue API, and R4 can never use a standing grant. Standing scope
binds complete input identities (path, digest, version) and exact output
prefix/version scopes; a content digest cannot transfer permission to another
artifact identity. Human-required R0 actions still need their declared human
or campaign authority source.
`approval-request/1.0` is a human-facing request with
`creates_authority=false`; legacy `ApprovalEvidence` and readiness records do
not prove signature, ledger inclusion, or current revocation. Only an
`authority-decision/1.0` produced from a trusted ledger receipt can carry
`execution_authority`, including POLICY-owned R0/R1 workflow actions. Each
human is bound to one signature verification whose
content is distinct from every other authentication proof. The requester is
authenticated by a separate immutable proof and cannot self-approve. Workflow
authority additionally requires a trusted-ledger proof for the exact clean
workflow-evaluation SHA; a structurally self-rehashed gate bundle is
non-authorizing. The decision seals the exact initial authority basis so fresh
revalidation cannot swap its source, grant, requester, human, workflow proof,
or signature evidence.
The policy fixes the maximum R4 receipt lifetime at 300 seconds. Ledger entry,
requester authentication, signature verification, and workflow-evaluation
verification references have exact role-specific artifact versions, and the
ledger head is a lowercase SHA-256.

Initial evaluation verifies the exact request/risk/context and allowed authority
source. Every actual executor dispatch, reconcile, and managed mutation then
calls `TrustedAuthorizationLedger.revalidate_and_reserve_current` immediately
before the side effect. The fresh receipt is bound to the distinct purpose,
decision, request, context, workspace observation, adapter/service identity,
ledger head/state, current signature and revocation evidence, kill switch,
exact risk and workflow-evaluation proof, cost/candidate reservation,
retry index, and idempotency key. A receipt for dispatch cannot be replayed for
reconcile or mutation. Missing, unavailable, stale, expired, superseded,
revoked, over-limit, or rebound state fails before the external call.
The executor receives an immutable runtime scope containing the actual subject,
adapter/provider, model, destination, cost/currency, candidates, and retry;
dispatch compares it exactly with the authority request and stores it for any
later reconcile.

The W02 bridge remains deliberately conservative: `managed_mutation` is fixed
at R4 in the target policy, requires a W04 dual-human ledger decision, and then
also requires the existing exact break-glass evidence and two-human binding.
W04 does not activate lower mutation tiers merely from a path or requester
label. The immutable mutation plan preserves the originating requester; the
W04 request must match it, and that requester cannot appear in either human
approver set. W06 exercises the completed W04 ports through a fixture-only
durable journal, clock/identity/kill-switch fakes, reservation settlement,
executor, publisher, and crash/TOCTOU recovery. Production ledger storage,
signatures, credentials, providers, and effect adapters remain outside this
program.

## Automated selection, integrated quality, and release handoff (W05)

W05 is an additive, non-side-effect automation plane. It does not reinterpret
legacy `candidate-ranking/1.0` or `edit-manifest/1.0`, alter the W04
characterization corpus, or cut the legacy orchestration path over before W06.

`quality-bundle/1.0` covers exactly nine target-owned dimensions for every
exact `MediaSubject`. A media subject binds path, byte SHA-256, byte length,
workspace-observation SHA-256, and an immutable observation receipt. Each
dimension binds a distinct evaluator identity and receipt. Stored fields are
structural only: production verification receives the bundle origin time
independently, re-resolves the media, and verifies every evaluation receipt at
origin and current consumer times through trusted runtime ports. Technical media,
continuity, platform compliance, explicit hard failures, and safety failures
cannot be averaged away. Remediation is limited to the exact failed
shot/component/dimension set, records immutable attempt receipts and
cumulative use, and stops on replay, no progress, regression, oscillation, or
the target two-retry ceiling.

`candidate-decision/1.0` requires at least two unique candidates per shot.
Auto-selection requires a passed current QualityBundle, a trusted current
confidence receipt for every candidate, current trusted-ledger W04 authority
for the separate `auto_select_candidates` action/capability, score at least
8000, confidence at least 8500, and a unique top margin at least 500 basis
points. The authority request envelope binds the complete selection input,
including workspace/channel/concept/episode, and its scope binds the same four
IDs plus every candidate media and confidence receipt. Verification first
reconstructs the complete original authority/timestamp lineage from an
independently supplied origin evaluation time, then performs a separate
current-authority check; current evidence cannot rewrite origin, including for
escalation or denied decisions that never carried authority.
Threshold equality passes; ties and lower values escalate. A downstream
consumer receives the original verification inputs and cleanly recomputes a
persisted decision at a separate trusted current-verification time. The legacy
projection is diagnostic-only,
`current_eligible=false`, `authority_effect=none`, and never claims a human
selection.

Release identity is cycle-free. `release-candidate/1.0` is built before its
authority request and binds exact final media, metadata, subtitle,
accessibility, thumbnail, release QualityBundle, CandidateDecision,
DestinationBinding, policy, GateContext, workspace observation, and exact
workspace/channel/concept/episode scope. Verification takes the original
candidate creation time as an independent expected input. W04 `approve_publish`
then consumes that exact scope, candidate, and constituent set, and
`release-assessment/1.0` first cleanly reverifies that entire candidate at the
assessment time and then binds the authority result. Initial W05 policy
requires exactly one current one-shot human principal; release campaign remains disabled.
Missing approval yields only a non-authorizing `ApprovalRequest`. Even a ready
assessment has `publish_performed=false` and `authority_effect=none`; there is
no publisher API in the W05 core plane.

The seven W05 artifact schemas and exact quality-policy resource are packaged
with semantic wheel verification. W06 adds seven closed runtime/migration
schemas and fixture adapters. Real evaluators/resolvers, credential systems,
provider execution, production publication, and production migration cutover
remain external.

## Durable fixture runtime and reversible migration (W06)

`video_factory.runtime` defines seven authority-free durable artifacts:
`execution-intent/1.0`, `execution-journal-event/1.0`,
`execution-receipt/1.0`, `publication-intent/1.0`,
`publication-receipt/1.0`, `projection-parity-receipt/1.0`, and
`migration-cutover-state/1.0`. Every public serializer validates its own
registered closed schema; duplicate keys, non-finite values, unknown fields,
identity rebound, and cross-field inconsistencies fail closed. The wheel now
contains 87 schemas and 82 registered artifact versions.

`video_factory_runtime` is additive and explicitly fixture-only. A boundary
marker binds an empty isolated root to one runtime ID. The concrete SQLite
journal uses `BEGIN IMMEDIATE`, `synchronous=FULL`, atomic idempotency claims,
append-only hash-chain events, stable claim-bound reservations, and an
append-only history of fresh timestamp-bound authority receipts. The same `(action kind, action id,
idempotency key)` with another request digest is rejected. Terminal exact
replay returns its receipt without a fresh effect check. A durable pre-effect
`planned`, `authorized`, or `reserved` record may continue only after every
current check is repeated. `dispatching`, `dispatched`, `partial`,
`reconciling`, and `uncertain` are reconcile-only and can never redispatch.
For mutation, the ordered effect IDs are part of the canonical intent identity;
the transaction that claims an effect revalidates the execution row, complete
event chain, and exact next intent-bound operation before writing a marker.
Success, failure, and reconciliation require one atomic event-plus-receipt
commit; the generic state API cannot terminalize may-have-started work.

The fixture executor validates exact request/output/cost/workspace semantics.
The managed filesystem executor rechecks no-follow file identity, bytes,
manifest CAS, containment, and link/reparse state at actual use and records
operation-level partial/uncertain evidence. POSIX effects use the exact
root-to-parent no-follow directory descriptor chain and handle-relative `*at`
operations; Windows effects use exact directory/file handles. Both persist fresh W04 reservation
and settlement records. A raw credential string or caller-minted opaque ID is
not authority: only a broker-issued, destination/service/purpose-bound current
lease may cross the effect boundary, and that lease cannot be serialized.

Publication remains separate from W05 readiness. A fixture publication first
recomputes current ReleaseCandidate/ReleaseAssessment eligibility, then
requires an exact R3 `ready_for_human_publish`/`publish` W04 request, current
workspace verification, service identity, kill switch, opaque credential
handle, journal, and settlement. It persists before/after workspace verifier
records and never treats the W05 assessment as authority.

Migration uses a durable hash-chained state with `legacy_only`,
`dual_read_compare`, `projection_read_only`, and `rolled_back`. A projection is
selectable only for read-only use after an exact trusted parity receipt and a
separate current activation verification. It remains non-current and
`authority_effect=none`. Generation zero permanently binds the exact legacy
artifact reference; every legacy/dual/rollback selection must match it, and a
rollback cannot choose an older activation receipt. The only parity verifier is a fixture-pinned exact-byte
corpus; every production consumer is `unregistered` and production activation
is disabled. Public identifier migration remains deferred to T90.

## Managed mutation plane (W02)

The normal flow is `ChangeRequest` → deterministic `MutationPlan` plus
structured semantic diff → external authority evaluation → immediate
`MutationPreSideEffectGuard` revalidation → runtime execution →
`MutationReceipt` → immutable `WorkspaceRevision`. The core implements the
contracts, pure planner, guard, and runtime Protocol only; it contains no
filesystem executor or approval issuer.

- Create requires observed absence. Replace/delete/move require the exact
  current source digest; move also requires destination absence. Caller input
  cannot lower risk. W02 treats every mutation, including every CREATE path, as
  R4 because a path string cannot prove lower semantic risk. R1/R2/R3 execution
  remains disabled. W04 now supplies the exact-request policy decision and
  trusted-ledger seam, but its target policy intentionally classifies
  `managed_mutation` as R4; no lower semantic classifier has been activated.
  W04 authority and R4 break-glass are both required.
- Before either authority or idempotency reservation, the W04 request ID,
  idempotency key, and core-owned digest of the complete canonical mutation
  plan must exactly match the W02 plan.
- Every plan binds the workspace revision, before-manifest digest, serialized
  base revision digest, policy digest, request digest, and idempotency key.
- Managed paths are canonical relative POSIX NFC strings and reject absolute,
  traversal, backslash, ADS/reserved-name, trailing-dot/space, case/Unicode
  collision, symlink, reparse, missing ancestor, and non-directory ancestor
  aliases, including Windows superscript-digit and console-device names.
- Create/replace authorization resolves the current immutable content object
  and rechecks object ID, byte length, and digest. Reused identical objects are
  resolved once, while one object ID mapped to conflicting bytes is rejected
  before reservation. Authorization preserves each unique object's exact
  resolver-evidence pair, and plan-aware validation requires complete set
  equality rather than merely counting evidence. An atomic trusted
  idempotency ledger reserves the exact key + plan + workspace observation;
  missing reservations, conflicts, and replays fail closed.
- Out-of-band drift produces `drift-report/1.0`, sets trust to `UNTRUSTED`, and
  invalidates dependent plans, authority, and QC while blocking generation and
  publish until reconciliation. Production consumers require the exact
  workspace ID, canonical `WorkspaceRevision` digest, manifest, and complete
  file observation; a caller's `TRUSTED` label alone has no authority.
- R4 break-glass is input evidence only: exact plan/workspace/revision/manifest
  scope, two distinct currently authenticated human principals whose ledger
  records bind the canonical break-glass request preimage, bounded validity,
  and trusted snapshot/incident/audit verification. Core never creates that
  evidence.
- `MutationReceipt` binds the complete execution-authorization digest,
  pre/post workspace-observation digests, atomic idempotency reservation,
  durable journal record, executor identity, and an explicit recovery plan for
  partial/failed/uncertain outcomes. Trusted revision promotion also receives
  the exact authorization and post-execution observation and compares every
  active file byte digest/length before creating immutable entries/tombstones.
- Standalone execution-authorization validation checks only closed shape and
  canonical identity. It is deliberately non-authorizing. Guard, executor, and
  revision consumers must call the plan-aware validator, which rechecks the
  exact plan binding, planned content evidence, and complete R4 two-human plus
  snapshot/incident/audit verification set.
- Revision origin is explicit: a genesis `reconciled_baseline` has no parent
  and requires immutable reconciliation evidence; a `managed_mutation`
  revision requires a parent and cannot masquerade as a baseline.

### Breaking change (core 0.x — allowed under plan-only directive)

| Removed (breaking) | Replacement |
|---|---|
| `storage.materialize(...)` → `WorkspaceInitResult` (copied files) | `storage.plan_materialize(...)` → `WorkspaceInitPlan` |
| `storage.export(...)` → `ExportResult` (wrote dir/zip) | `storage.plan_export(...)` → `WorkspaceExportPlan` |
| `WorkspaceInitResult` / `ExportResult` | `WorkspaceInitPlan` / `WorkspaceExportPlan` (+ status enums, `CopyOperation`) |
| `WorkspaceInitEngine.materialize` / `WorkspaceExportEngine.export` | `.plan_materialize` / `.plan_export` |
| CLI `init`/`export` payload fields implying writes (`files_copied`, `files_exported`) | plan fields (`operations`/`files`, `executed=false`, plan `status`) |
| doctor purity via child-process launch of `tools/check_core_purity.py` | in-process `video_factory.security.purity.scan_repository` (CLI tool remains a thin wrapper) |

Validation/refusal logic is **not** weakened: the same nonempty-target, schema, frozen-index, and sensitive-pattern
checks still run; only the post-pass action changed from write → plan.

## Configuration documents

The persisted models are `WorkspaceConfig`, `ChannelConfig`, `ConceptConfig`, and `EpisodeConfig`. Each document
has its own `artifact_version`, an independent `config_contract`, one opaque scope identifier, optional closed
`settings`, and optional `extensions`.

Core-owned settings are deliberately small:

- `media.duration_seconds`, `media.aspect_ratio`, and `media.platforms` are values supplied by configuration.
  The package declares no duration, shape, or platform default.
- `identity.recurring_character_ids` is an array of opaque identifiers. An empty array is valid and explicitly
  represents a scope with no recurring identity.
- `execution.mode` records an opaque requested mode. It has no package default and does not by itself grant an
  external action.

Every core-owned object is closed. Unknown fields fail validation. The only open data is
`extensions.<namespace>.payload`; its core-owned wrapper still requires exactly `contract_version` and `payload`.
A namespace must be registered with an owner-supplied validator before merge. The core invokes that validator,
then preserves and hashes the payload without interpreting it. A payload cannot add or relax a core invariant.

The schemas are in `schemas/`. `config-layer.schema.json` is the single shared definition source used by the four
layer schemas and `effective-config.schema.json`.

## Merge and provenance

Precedence is fixed from least to most authoritative:

1. `core_defaults`
2. `workspace`
3. `channel`
4. `concept`
5. `episode`
6. `runtime_override`
7. `human_decision`

Input order is irrelevant; the merger sorts by this contract. Only one source per layer and one occurrence of a
JSON object key are allowed. Objects merge recursively, like-typed scalar values replace, and arrays replace as a
whole. Arrays are never appended implicitly. A type conflict or `null` deletion fails the merge.

Runtime overrides are fail-closed: callers must pass an explicit allowlist of leaf JSON Pointers, and any other
runtime pointer aborts snapshot creation. An explicit human decision is a configuration provenance layer, not
approval evidence. Approval evidence remains a separate immutable artifact.

Provenance maps every effective leaf JSON Pointer to its winning `layer` and `source_id`. Object members inherited
from lower layers keep their own winners. Each extension namespace is one opaque leaf for merge and provenance,
so the core never recursively combines owner payload semantics.

## Effective snapshot and deterministic hash

`EffectiveConfigSnapshot` uses `artifact_version: effective-config/1.0` and contains:

- scope identifiers;
- independent distribution, core-contract, rules, policy, and config-contract versions;
- the core lock binding;
- ordered source path and source digest records;
- the closed merged values and per-pointer provenance;
- applied runtime allowlist constraints; and
- `effective_config_sha256`.

Source digests and the effective digest use `canonical-json-v1`:

1. Objects are sorted by Unicode key value. Arrays retain their declared order.
2. Output is UTF-8 with no BOM, insignificant whitespace, or trailing newline.
3. Strings use JSON escaping. Only finite numbers are allowed.
4. Integers, decimals, and finite floating values use plain base-10 form; redundant fractional zeroes are removed,
   and negative zero becomes zero.
5. A source digest is calculated from the complete parsed source document, so key order, indentation, and file
   line endings do not affect it.
6. The effective digest covers artifact version, scope, independent versions, bindings, ordered source metadata,
   effective values, provenance, and constraints. `created_at` and the digest field itself are excluded, preventing
   clock time and self-reference from changing the logical configuration identity.

The snapshot mapping is JSON Schema-valid JSON data. Persistence, version allocation, and no-overwrite storage are
separate storage responsibilities; this package does not silently write a file as a side effect of merging.

## Version and migration policy

Version fields are not aliases:

- `artifact_version` is `<kind>/<major>.<minor>` and versions one artifact structure and meaning.
- `rules_version` is opaque provenance supplied by the workspace. It is not parsed as a package or artifact
  version.
- `core_distribution` is the installed package semantic version.
- `core_contract`, `config_contract`, and extension contracts independently express machine compatibility.
- `policy_version` identifies the applied policy bundle independently of the rules provenance.

Artifact major migration IDs use `<artifact-kind>/<from-major>-to-<to-major>/<ordinal>`. A migration advances one
major at a time, is deterministic for the same input bytes and migration ID, writes a new artifact instead of
editing the source, records source path/hash/migration/core version, never disguises a rules or policy change, and
never performs an automatic downgrade. An unknown major fails closed. Each schema records these rules in its
`x-artifact-migration` annotation.

## Production artifact schemas (planning → generation → publish prep / handoff)

Core validates **structure**, not creative taste. Channel-specific marketing policy, provider names, duration
ranges, and aspect ratios stay outside these schemas (policy pack or config settings). Adding a
`schemas/{family}.schema.json` with `properties.artifact_version.const` is enough for automatic registry
registration; the runner source tree need not change.

Common envelope (document families):

- `artifact_version`: `<family>/<major.minor>` (const per schema, e.g. `storyboard/1.0`)
- `rules_version`: opaque provenance string (not parsed as a package version) — required on most families;
  append-only `handoff-event` omits it and carries provenance in the event payload when needed
- `extensions`: optional closed wrapper map; only `extensions.<namespace>.{contract_version,payload}` is open inside `payload`

File naming: `schemas/{family}.schema.json` for family `artifact_version` left side. Shared `$defs` live in
`artifact-common.schema.json` (not itself a document kind). The registry loads every `*.schema.json` that declares
`properties.artifact_version.const` and maps that const → schema. An unregistered version **fails closed**.

### Planning / storyboard / references

| Family (`artifact_version`) | Core structural fields | Explicitly not forced |
|---|---|---|
| `brief/1.0` | `episode_id`, one-line `summary`, `hook`, `development`, `ending`, `risks[]`; optional `series_link`, optional `marketing_reasons[{reason_id,statement}]` | 4 Reasons names/count; Stop/Stay/Share/Series are channel policy via `marketing_reasons` or `extensions` |
| `idea-candidates/1.0` | opaque `candidate_id`, `title_working`, `premise`, scorer/writer `roleId` | pillar enums, candidate count caps, marketing reason taxonomy |
| `idea-scores/1.0` | blind scores: opaque candidate ids, numeric `rubric` map (keys free), `scored_by` role | fixed rubric key set, provider/role enums, score ceiling |
| `idea-scorecard/1.0` | aggregated scores + `selection_policy.automatic_selection: false` and null `selected_candidate_id` | automatic winner selection; max score constants |
| `storyboard/1.0` | `shots[]` with `shot_id`, `duration_sec` (positive number only), opaque `narrative_role`, `characters[]` (empty allowed), optional opaque `location`, optional `camera` object, structural `action`, free-text `creative_direction`, and optional opaque `end_state_elements[]` as 2.1 carryover evidence | shot count range, total duration range, aspect ratio, comedy-timing enums |
| `storyboard-review/1.0` | hash-bound `subject` (`path`+`sha256`+`artifact_version`), creator/reviewer roles, `verdict` ∈ `pass`/`fail`/`uncertain` (aligned with `ReviewVerdict`), `findings[]` | concrete AI tool identity enums |
| `storyboard-approval/1.0` | `approved_by_human`, `approver_role`, `approved_at`, `bound_artifacts[]` (path+sha256+artifact_version; same binding idea as `ApprovalRequirement.bound_artifacts`) | channel-specific actor const strings |
| `storyboard-approval/2.0` | granted human evidence with requirement/evidence IDs, state, record hash, effective-config hash, and exact current storyboard/review bindings | pending requirements or a packet-local boolean as approval |
| `reference-manifest/1.0` | human selection metadata + `assets[{path,sha256}]` where `path` is repository-relative only (absolute / drive / `..` rejected) | fixed `05_references/` prefix, image extension whitelist |
| `reference-review/1.0` | per-asset `verdict` + free-text `reasons[]` | bible-match / composition checklists (channel QC policy) |

### Generation / QC / edit / publish / handoff

| Family (`artifact_version`) | Core structural fields | Explicitly not forced / forbidden |
|---|---|---|
| `generation-packet/1.0` | per-shot free-text `prompt`, `candidates` (integer ≥ 1), optional `reference_assets[{path,sha256}]`, opaque `capability_id`, `provider_plans[].adapter_id` (opaque), optional `quota_plan.budgets` as map of `{unit,amount}` | concrete media adapter enums, aspect-ratio enum, candidate max caps, named credit pools |
| `generation-packet/2.0` | storyboard hash binding; explicit output dimensions; per-shot source id, capability, first-frame path/hash/dimensions/before-state, continuity/master-plate facts, and render dependencies | provider-specific duration/aspect constants; those come from an injected constraint profile |
| `generation-packet/2.1` | adds per-shot `first_frame.depicted_elements` and `continuity.carried_elements`; strict feasibility compares them with the preceding hash-bound storyboard shot's `end_state_elements` so an unmatched empty declaration cannot pass | element vocabulary (opaque, owner-assigned); pixel-level proof |
| `packet-review/1.0` | same review pattern as `storyboard-review` (`subject` hash bind, `verdict`, `findings`) | tool identity enums |
| `packet-approval/1.0` | same approval pattern as `storyboard-approval` (`bound_artifacts[]`, `approver_role`) | channel actor const strings |
| `approval-requirement/1.0` | pending gate request with deterministic requirement ID, capability, input hashes, and effective-config hash; `creates_evidence: false` | approver/timestamp placeholders or approval authority |
| `generation-feasibility-review/1.0` | packet/storyboard/profile bindings and the check kinds emitted per shot (six for `generation-packet/2.0`, seven for `2.1`, which adds `first_frame_state_carryover`); verdict pass/fail/inconclusive | inferred provider behavior |
| `packet-approval/2.0` | granted human evidence exactly bound to packet, required reviews, and feasibility evidence | a non-authoritative `approved_by_human` packet flag |
| `external-call-reservation/1.0` | reservation **record**: `event_id`, `reserved_at`, `actor_role`, opaque `adapter_id`/`capability_id`, `idempotency_key`, `status`, `bound_artifacts[]`, optional `quota_snapshot` | auto-trigger/dispatch fields; hard-coded daily quotas |
| `candidate-ranking/1.0` | deterministic sort: `ranking_policy[]`, per-shot ranked candidates with numeric `metrics` map | **no** `final_selection` / auto-choice fields (human selects) |
| `shot-qc/1.0` | `measurements[]` / `constraints[]` / `findings[]` aligned with `qc.contracts` (`Measurement`/`Constraint`/`Finding`); `expectations_source` points at config, not literal channel numbers | fixed resolution/fps/duration constants in schema |
| `shot-qc/2.0` | adds required `shot_id` and allows pass/warn/fail/inconclusive so every packet shot can be gated independently | treating warning as failure or missing data as pass |
| `continuity-qc/1.0` | binds **several** shot outputs at once; per-element observations (`relative_scale` / `orientation_shape` / `presence`) at first/middle/last sample points, pairwise comparisons, exact measurement values/units, complete policy inputs, and per-comparison judgments; sufficient for deterministic rejudgment | tolerance defaults, frame-extraction execution, element vocabulary |
| `edit-manifest/1.0` | human-selected `input_clips[]` (path+sha256), `audio_required`, optional free-text `target_platforms[]` | platform brand enums as required vocabulary |
| `rough-cut-report/1.0` | local assembly status, ranking bind, inputs, `output_path`, optional local-tool flags and command argv | publish/upload execution flags |
| `final-delivery/1.0` | selected output hash, lineage references, technical-QC reference, and pass/warn technical verdict | implicit “latest file” selection |
| `final-review/1.0` | creator/reviewer-separated review bound to the current final-delivery hash | self-review or stale final review |
| `publish-metadata-draft/1.0` | draft title/description/tags, optional disclosure text, `human_review_required: true` | **no** account id, auto-upload, or publish-execution fields |
| `publish-approval/1.0` | granted evidence bound exactly to final delivery, required final reviews, and metadata | upload or publish execution |
| `generation-day-brief/1.0` | `approval_items[]` aggregation, optional quota structure, `automatic_approval_performed: false`, `generation_executed: false` | fixed gate-kind enum, hard quota numbers |
| `handoff-event/1.0` | append-only: `event_id`, `occurred_at`, `actor_role`, opaque `event_type`, free `payload` | channel pipeline state enums as closed vocabulary |
| `handoff-task/1.0` | task envelope aligned with adapter **request** concepts: `task_id`, `input_artifacts`, `allowed_outputs`, `capability_allowlist`, `idempotency_key`, `required_actor_role`, `human_gate_required` | concrete executor/tool enums; `request_id` reserved for adapter calls (use `task_id` here) |
| `handoff-result/1.0` | result envelope aligned with adapter **result** concepts: `outcome` ∈ `SUCCEEDED`/`REJECTED`/`FAILED`/`EXTERNAL_UNCERTAIN`/`AWAITING_HUMAN`, `external_reference`, `measured_cost`, `uncertainty`, optional `request_id` | concrete adapter id enums |
| `next-step/1.0` | next action guidance: `next_actor_role` (opaque role id), `action_type`, `instructions`, relative `target_paths[]`, `approval_required`, `auto_execution` | locale-only instruction fields; auto-execution with approval required |

### Analytics / retro (2)

| Family (`artifact_version`) | Core structural fields | Explicitly not forced / forbidden |
|---|---|---|
| `analytics-record/1.0` | opaque `episode_id`, `collected_at`, `collector_role`, `checkpoint_window{window_id,definition}` (both data), free `metrics` map (`metric_key` → number\|null), optional `publish_record_ref` bind | named marketing reason taxonomies; fixed checkpoint window vocabulary; external API clients |
| `retro-report/1.0` | `policy_id`, `checkpoint_window_ids[]` (from records used), `evaluations[]` with opaque `hypothesis_id` + closed `comparator`/`verdict`, aggregate `counts` | hard-coded hypothesis set size/names; wall-clock computation timestamps; treating null metrics as failure |

Contract alignment notes:

- **Approvals:** `packet-approval` and `external-call-reservation` use `bound_artifacts` (path+sha256+artifact_version), matching `ApprovalRequirement.bound_artifacts` and ADR-004 hash binding; reservation adds `idempotency_key` for the request-envelope concept without becoming a dispatch trigger.
- **Providers:** `handoff-task` uses the same *ideas* as `RequestEnvelope` (`input_artifacts`, `allowed_outputs`, `capability_*`, `idempotency_key`) but keeps the name `task_id` because a handoff task is not necessarily one adapter request. `handoff-result` reuses `outcome`, `external_reference`, `measured_cost`, and `uncertainty` with the same meanings as `ResultEnvelope`.
- **QC:** `shot-qc` serializes `Constraint` / `Measurement` / `Finding` shapes from `video_factory.qc.contracts`; expected technical values are referenced via `expectations_source` (config), not hard-coded in the schema.

Runner surface (`video_factory.artifacts`):

- `ArtifactSchemaRegistry` / `get_default_registry()`
- `validate_artifact(document, artifact_version=None)` → structured `ArtifactValidationResult` (field paths)
- `validate_artifact_directory(path)` → `BatchValidationReport` (passed/failed/skipped counts)

CLI `validate`:

- **Config (unchanged):** `--layer workspace|channel|concept|episode <path>`
- **Artifact:** omit `--layer`, pass a document path (uses document `artifact_version`)
- **Batch:** `--directory <dir>` validates every `*.json` with an `artifact_version`

## Other artifact contracts

- `ArtifactReference`: relative path, SHA-256 digest, and artifact structure version.
- `CapabilityDescriptor`, `RequestEnvelope`, `ResultEnvelope`: adapter discovery plus hash-bound input/output and
  uncertainty evidence.
- `ApprovalEvidence`: immutable evidence bound to the exact requirement and referenced artifact hashes.
- `QCReport`: deterministic findings evaluated only against injected policy constraints.
- `CatalogEntry` and ledger records: exact paths and hashes; directory discovery is not part of the contract.

## Generic quality lint contract

Core does **not** know channel character bibles, shot counts, avoid-term lists, or provider budgets. Those remain
channel-owned rule *data*. Core only evaluates injected rules:

| Core type | Meaning |
|---|---|
| `VerbatimRule` | `target_field_pointer` (JSON Pointer) must match `expected_text_sha256` (and optional full `expected_text` byte-for-byte). Optional `source_path` + `source_sha256` detect source drift. |
| `SourceLockRule` | file at `source_root / source_path` must currently hash to `source_sha256` |
| `LintRuleSet` / loader | channel ships a JSON rule file (`rules[]` with `kind: verbatim \| source_lock`); YAML extension accepted only when JSON-compatible (stdlib-only) |
| `run_lint` → `LintReport` | findings reuse `video_factory.qc.Finding` / `Measurement`; `passed` is all-findings conjunction |

## Generation order-sheet contract

`render_generation_sheet(packet_doc, readiness=..., current_context=...,
evaluated_at=...)` turns a packet into human copy-paste Markdown. Without an
`authorization_ready` plan bound to the exact canonical packet digest, current
seven-digest `GateContext`, and a timezone-aware instant inside the aggregate
approval window, the title always contains `DO NOT GENERATE`. A context-free
legacy `ready` plan remains useful for non-authorizing sequencing but cannot
remove that warning. The packet's compatibility boolean is displayed but never
grants authority.

Determinism rules (same input → byte-identical output):

1. UTF-8, LF only; no wall-clock timestamps.
2. Fixed section order: header → output/quota → shots (document order) → footer marker.
3. Free maps enumerated with Unicode-sorted keys; `provider_plans` labels sorted.
4. Each shot `prompt` is emitted **verbatim** inside a fenced block (no summary or rewrite).
5. Header and HTML comment stamp `packet_sha256` from `canonical-json-v1` bytes of the packet.
6. An authorized sheet stamps the gate-context digest, aggregate validity
   window, feasibility evidence, and human-approval evidence references.
   Missing, stale, future-issued, or expired readiness raises instead of
   silently granting authority.

## Encode plan contract (OD-005 — no execution)

`schemas/encode-request.schema.json` and `schemas/encode-command-plan.schema.json` are **contract** schemas, not
production artifact families (no `artifact_version` const → not auto-registered by the artifact registry).

| Core type | Meaning |
|---|---|
| `EncodeRequest` | profile id, role-tagged inputs (video/audio/subtitle/overlay), optional video/audio filters, output spec, overwrite observation, and shortest policy |
| `EncodeProfile` (data) | `rough_cut` concatenates video; `final` re-encodes one video and optionally muxes one soundtrack; media values remain overridable |
| `EncodeCommandPlan` | argv/string plus post-encode checks and fallback profile references; **`executed: false` always** |
| overwrite | `refuse` + `output_exists` → `rejected_output_exists` (no argv); `allow_version_suffix` requires `version_suffix` |

Core encode/lint/sheets modules must never import or invoke process-launch helpers. Execution is a
human step or a later approved adapter.

## Adapter execution contract

The adapter surface implements ADR-004 without concrete bindings. `CapabilityDescriptor` is the shared discovery
record for an opaque adapter ID, provider/executor kind, contract version, capabilities, supported execution
modes, input/output artifact versions, side effects, and uncertainty model. `RequestEnvelope` binds a capability
request to its effective-config digest, immutable input references, allowed outputs, idempotency key, and separated
creator/reviewer roles. `ResultEnvelope`, `ExternalReference`, `CostMeasurement`, `UncertaintyEvidence`, and
`Outcome` preserve output provenance, both external request/session identifiers, measured-or-unknown cost, and
uncertain reconcile state without translating a timeout into success or failure.

The provider and executor abstractions deliberately remain separate:

| Core type | ADR-004 decision implemented |
|---|---|
| `ProviderAdapter`, `ProviderPlan`, `ProviderPreview` | deterministic validation, provider-unit estimate and local-only preview |
| `HumanHandoff`, `ProviderHumanResult` | prepare human instructions and expected output names, then stop at `AWAITING_HUMAN` without external dispatch |
| `ReadOnlyStaging`, `ExecutorDispatchContext` | exact read-only inputs, allowed output contract, and tool/capability allowlists before executor dispatch |
| `ExecutorAdapter`, `NormalizedEvent` | creator/reviewer separation, stream normalization, timeout uncertainty, and reconcile-before-retry |
| `ExecutionModeLimits`, `EffectiveExecutionMode` | intersect channel, selected-mode, and adapter maxima during effective-config merge |
| `OrchestrationPolicy`, `OrchestrationGuard` | bind effective mode, request, seven-digest current context, explicit evaluation time, and immutable human evidence before reservation or adapter selection |
| `AdapterBinding`, `InMemoryCapabilityRegistry` | resolve a profile-supplied opaque binding and enforce capability plus adapter kind |
| `enforce_adapter_dispatch` | immediately before any external process, repeat mode/kind/capability checks and require the matching guard authorization, current context, evaluation time, and unexpired evidence window |

Provider descriptors cannot advertise `automated` in this contract. A provider can validate, estimate, preview,
prepare a human handoff, and ingest a human-downloaded result; it has no external generation implementation.
Executor dispatch and external reconciliation are available only in effective `automated` mode, with read-only staging, explicit allowlists,
and an `OrchestrationAuthorization` matching request ID, capability, mode, current context digest, and dispatch
time. The authorization also carries a canonical digest over every `RequestEnvelope` field: input artifacts,
allowed outputs, idempotency key, roles, capability, mode, config, and request ID. Calling `dispatch` or
`reconcile` directly without that exact-request authorization fails before `_dispatch_stream` or
`_reconcile_external` is entered. Unresolved state also retains the exact original request and external reference;
a same-key request substitution or conflicting reference is rejected.
An uncertain executor idempotency key remains locked until `reconcile()` reaches a settled outcome.

Missing or unknown execution limits normalize to `human_only` as a fail-safe. This is not a production workflow
default: no workflow profile is selected by the package. A configuration field or extension boolean cannot
increase the intersection result.

## Brand entity projection contract

`brand-entity/1.0` implements only the shadow-evidence boundary. Its common envelope requires an opaque entity ID,
`character` or `location` kind, canonical status, opaque rules provenance, one exact source path and byte digest,
declared heading-bounded extraction ranges, and an ordered array of fixed sentences. Every fixed sentence carries
its own role, ordered consumer IDs, source section, verbatim UTF-8 text, and digest. Duplicate roles fail validation;
the package never merges entries or chooses a representative sentence.

The `data` member is closed and kind-discriminated. `CharacterData` records a display name, design status, exact
visual-reference path, scale anchor, and ordered invariants. `LocationData` records a display name, ordered reference
tokens, usage, and ordered notes. The reference values remain owner data; the core assigns no character, place, or
consumer vocabulary.

The following type-to-decision mapping makes each abstraction traceable to the staged projection decision:

| Core type | Decision implemented |
|---|---|
| `SourceSectionRange`, `EntitySource` | bind a shadow to exact Markdown bytes and explicit extraction bounds |
| `FixedSentence` | preserve multiple role-specific blocks, their consumers, order, and byte digests without selection |
| `CharacterData`, `LocationData`, `BrandData` | use one envelope with closed kind-specific extensions while owner values stay outside core |
| `BrandEntity`, `EntityKind`, `CanonicalStatus` | distinguish shadow evidence from a future canonical source without changing authority |
| `ProjectionField`, `FixedSentenceObservation`, `ProjectionSnapshot` | represent comparable source, shadow, and rendered observations without repair |
| `FieldComparison`, `FixedSentenceComparison`, `OrderComparison`, `RoundTripReport` | expose missing values, ordering changes, and UTF-8 byte changes as structured evidence |
| `MarkdownProjectionRenderer` | produce a deterministic human view and copy fixed-sentence payload bytes unchanged |
| `RoundTripChecker` | compare all three observations and report differences instead of normalizing them |
| `BrandCatalog`, `CatalogEntity`, `CatalogSourceFormat` | give consumers one logical lookup boundary with an explicit source format |
| `MarkdownCatalogEntry`, `FixedSentenceExtractor`, `MarkdownBrandCatalog` | make the current production implementation read one declared Markdown path and hash only; no glob, merge, or modification-time choice |
| `NonProductionEntityError`, `require_production_eligible` | reject `shadow` structured data when offered as production input |

The generated view contains length-delimited UTF-8 blocks. The parser reads the declared byte count and fails on a
malformed trailer; it does not normalize line endings, whitespace, or text. Round-trip comparison covers modeled
fields and fixed sentences. Owner-side extraction is intentionally injected because interpreting arbitrary Markdown
headings and narrative prose is not a generic core responsibility.

## Distribution lock and wheel plan (ADR-001)

Channel workspaces pin the installed core via a root `core.lock` (UTF-8 TOML, `lock_format = 1`). The core
package **computes** lock text and compatibility verdicts; it does not write `core.lock`, build wheels, or run
`pip install`.

### `core.lock` shape

```toml
lock_format = 1
selected_distribution = "video-production-core"

[[artifacts]]
role = "core"
name = "video-production-core"
version = "<semantic-version>"
contract_version = "<major.minor>"
source_commit = "<40-hex>"
path = "vendor/core/<version>/<wheel-file>"
sha256 = "<64-hex>"
requires_python = "<version-range>"

[[artifacts]]
role = "dependency"
name = "<distribution-name>"
version = "<exact-version>"
path = "vendor/core/<version>/<wheel-file>"
sha256 = "<64-hex>"
```

Rules enforced by `build_core_lock` / `parse_core_lock`:

1. `path` is repository-relative POSIX under `vendor/core/`; `..` and absolute paths are rejected.
2. Exactly one `role = "core"` artifact; `selected_distribution` must equal its `name`.
3. Core artifacts require `contract_version`, `source_commit`, and `requires_python`; dependency artifacts omit them.
4. Versions are exact pins (semantic version strings). Floating ranges in lock tables are rejected.

### Determinism (`build_core_lock`)

Same inputs → identical UTF-8 bytes:

1. LF only (`\n`); no BOM; exactly one trailing newline.
2. Header key order: `lock_format`, `selected_distribution`.
3. `[[artifacts]]` sorted by role (core first), then name, version, path.
4. Per-table key order fixed; core-only keys omitted on dependency tables.
5. Paths normalized to POSIX; digests lowercased hex before emit.

### Compatibility (`verify_lock_against_installed`)

| Status | Meaning |
|---|---|
| `compatible` | installed distribution version and contract match the lock pin |
| `upgrade_available` | same **contract major**; distribution version differs — non-breaking; lock still pins old path |
| `breaking` | contract **major** differs (ADR-001 breaking definition as data) |
| `python_mismatch` | installed Python does not satisfy lock `requires_python` (OD-003: `>=3.12,<3.13`) |
| `invalid_lock` | document or inputs cannot be validated |

`forces_newer_install` is always `false`. A lock pin is the authority for channel assets: a newer installed core
does not rewrite or force replacement of `vendor/core/<old>/` wheels until a human replaces `core.lock`.

Align with `EffectiveVersions.core_distribution` / `core_contract` and `EffectiveBindings.core_lock_sha256`
(ADR-003): after install, distribution and contract versions must match the lock; effective-config binds the
lock digest.

### Wheel build · vendor placement · upgrade · rollback (human commands)

Core only emits a `WheelBuildPlan`. Humans (or channel-side tools) execute:

```text
# 1) From a clean tagged commit in the core repository
python -m build --wheel --outdir dist

# 2) Copy into the channel workspace without overwriting same version
#    expected: dist/video_production_core-<version>-py3-none-any.whl
#    target:   vendor/core/<version>/video_production_core-<version>-py3-none-any.whl
#    verify SHA-256 before and after copy (OneDrive lock: retry ≤3 then stop)

# 3) Offline install only (no network index)
python -m pip install --no-index --find-links vendor/core/<version> video-production-core==<version>

# 4) Run core contract tests + channel compatibility + known-failure baseline (273/9 must not grow)

# 5) Replace root core.lock in a single commit only after tests pass
#    (use build_core_lock(...) text; do not hand-edit digests)

# Rollback: restore previous commit's core.lock and reinstall the preserved older wheel.
# Do not delete the newer wheel (keep for investigation). Contract-major upgrades are never auto-rolled back.
```

Library entry points: `plan_wheel_build(version)` → argv + `vendor_relative_path` + placement notes;
`build_core_lock(artifacts)` → lock file text for the human to write.

### Path portability

`require_relative_artifact_path` / `require_vendor_core_path` / `map_archive_member_to_extract_relative` reject
absolute paths and `..` (zip-slip). `normalize_frozen_path` remains the casefolding membership key for frozen
indexes. Host-absolute literals remain forbidden by `tools/check_repo_isolation.py`.

## Breaking changes and current limit

The distribution uses semantic versions. A minor contract change may add an optional field while preserving every
existing required meaning. A major bump is required when an existing consumer cannot read or enforce the same
meaning. Weakening approval, no-overwrite, path containment, canonical hashing, idempotency, immutable-log, or
fail-closed behavior is breaking even if the Python call shape is unchanged.

Configuration validation, merge, provenance, canonical hashing, snapshot mapping, adapter contract behavior,
production artifact schema validation (twenty-five production families: nine planning/storyboard/reference plus
fourteen generation/QC/edit/publish-draft/handoff families plus two analytics/retro families, plus registry runner),
deterministic retro evaluation over injected metrics, doctor schema/tool/package diagnostics (observation only),
plan-only workspace init/export planners, plan-only QC/approval/orchestration planners, config draft builders,
ADR-001 distribution lock builder/verifier and wheel build planner, and the four execution-mode enforcement hooks
are implemented. The CLI registry exposes seventeen general commands: twelve are implemented in the plan-only sense
(return plans/documents/observations; never write or execute media tools); recovery commands are contract-only
intent evaluators with mode fail-closed and retry idempotency; only `migrate` remains `not_yet_backed`. Concrete
adapters, full workflow execution, human approval evidence creation, media measurement execution, **Plan execution**
(materialize/export/encode/wheel-build runtimes), and live `core.lock` materialization remain outside this package.
A mode request becomes effective only after the three-way safety intersection; no production workflow mode is
selected by default.

## Analytics and retro contract

Core evaluates **injected** metric snapshots against **injected** verification policy. It does not collect metrics
from external video platforms, does not hard-code marketing reason names, and does not hard-code observation
window labels. Those values are channel policy data.

| Core type | Meaning |
|---|---|
| `AnalyticsRecord` | one post-publish snapshot: opaque episode id, optional publish-record bind, collector role, checkpoint window (opaque id + free definition), `metrics` map with null = not collected |
| `VerificationRule` | opaque `hypothesis_id` + `metric_key` + closed `comparator` (`gte`/`lte`/`gt`/`lt`/`eq`/`between`) + numeric threshold (or inclusive range) + missing-metric policy (`inconclusive` only) |
| `RetroPolicy` | ordered rules plus optional preferred window id filter |
| `compute_retro(records, policy)` | pure function → `RetroReport` with per-rule `supported`/`refuted`/`inconclusive` and aggregate counts |
| `analytics-record/1.0`, `retro-report/1.0` | JSON Schema families registered like other production artifacts |

CLI analytics command backing is **not** implemented in this package yet; import the library surface directly.

## Doctor diagnostics (additive)

`doctor` keeps its original payload fields and adds:

| Field | Meaning |
|---|---|
| `schema_registry` | in-process scan: schema file count, registered `artifact_version` count/list, load failure list |
| `optional_tools` | `ffmpeg`/`ffprobe` observed via `shutil.which` as `present`/`absent` (absence does **not** fail doctor by itself) |
| `package_contracts` | `core_version`, `core_contract`, `config_contract` summary |

Purity is evaluated **in-process** via `video_factory.security.purity.scan_repository` (same summary line format as
`tools/check_core_purity.py`). Doctor does not launch a child process for purity.

live_health tests may observe host values but must not assert tool presence or absence.

## CLI command contract

| Status | Commands |
|---|---|
| `implemented` (plan-only) | `doctor`, `validate`, `init`, `export`, `qc`, `approve`, `run`, `status`, `new-channel`, `new-concept`, `new-episode`, `review` |
| `contract_only` | `retry`, `resume`, `invalidate`, `reopen` |
| `not_yet_backed` | `migrate` |

Honest meanings for promoted commands:

| Command | Returns | Does **not** |
|---|---|---|
| `qc` | `QCPlan` (+ optional `QCJudgment` over injected measurements) | run ffprobe/ffmpeg |
| `approve` | `ApprovalRequirement` (+ optional evidence-binding check) | create `ApprovalEvidence` / auto-approve |
| `run` | `NextStepPlan` (`transition_applied=false`) | transition workflow or execute stages |
| `status` | `EpisodeStateObservation` | mutate state |
| `new-channel` / `new-concept` / `new-episode` | validated config document mapping | write files |
| `review` | `ReviewRequest` document | perform the review |

Mode-independent commands are `doctor`, `validate`, `init`, and `export`. Every other command requires an explicit
workflow mode argument; missing mode raises the same fail-closed error as `resolve_workflow_policy(None)`.
Recovery policy checks reject Rapid for all four recovery commands, allow Standard for `retry`/`resume`/`reopen`,
and restrict `invalidate` (including cascade) to Controlled where destructive approval and immutable audit apply.

## QC plan engine

| Type / function | Meaning |
|---|---|
| `Expectation` | injected expectation with applicability, severity, boolean/numeric/string operands, and primary/fallback measurement hints |
| `build_qc_plan(constraints, expectations)` | pure plan containing non-executing primary and fallback method data |
| `judge_measurements(plan, measurements)` | pure pass/warn/fail/inconclusive/not-applicable judgment |
| Severity/applicability | failed INFO → pass with a recorded finding and does not lower overall; failed WARNING → warn; failed ERROR → fail; missing → inconclusive; declared N/A remains separate |

`Finding.passed` records whether the constraint expression was satisfied,
independently from the severity-adjusted judgment status. Therefore an INFO
violation is represented as `Finding.passed = false` with status `PASS`; its
message identifies it as an informational finding so the two meanings are not
confused.

## Approval requirement builder

| Type / function | Meaning |
|---|---|
| `build_approval_requirement(kind, artifacts, effective_config_sha256)` | binds path+sha256 artifacts; never invents evidence |
| `requirement_to_mapping` / `requirement_from_mapping` | `approval-requirement/1.0`; never inserts approver or timestamp placeholders |
| `approval_evidence_to_mapping` | serializes already supplied granted human evidence; never creates evidence |
| `validate_evidence_binding` | authority check requiring exact capability/artifacts, flat+nested config consistency, all seven current-context digests, explicit timezone-aware evaluation time, and `approved_at <= evaluated_at < expires_at`; legacy documents remain loadable but cannot authorize |

## Orchestration planner

| Type / function | Meaning |
|---|---|
| `observe_episode_state(snapshots)` | validates caller-observed path/hash/document envelopes and builds an explicit-current graph |
| `build_generation_readiness` | preserves context-free structural planning compatibility while exposing a separate `authorization_ready` result only for current-bound, unexpired approvals |
| `plan_next_step(observation, workflow_mode)` | context-free overload preserves non-authorizing sequence calculation; context/time overload rejects stale authority; both block failed reviews/QC and never execute |
| `next_step_to_mapping` | deterministic `next-step/1.0`; requires rules provenance and keeps `auto_execution=false` |
