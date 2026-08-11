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
| W00 Bootstrap/Baseline | passed | BOOT-001..005 | `d00856550f7f66618e3d365725e6b2d9054380c1` |
| W01 Trust Boundary | passed | P0-PKG-001, P0-JSON-001, P0-AUTH-001 | `47d968d3cf8014573b349ad858d6a04bdf724f94` |
| W02 Managed Mutation | passed | MUT-001..004 | `17107e815996e18032132a739140aeeae0b2716e` |
| W03 Director/Blueprint | passed | BP-001, DIR-001, DIR-002, BP-002 | `254e20fdb63c77d6e84d746664a5f1a0a9524c03` |
| W04 Workflow/Authority | passed | WF-001, AUTH-001, AUTH-002, WF-002 | `5bc36f63d51e0a78c42603fa13946b691ed67585` |
| W05 Automation/Quality/Release | passed | SEL-001, QA-001, REL-001 | `22df49acf9fb0370b78b0ed5a4e42373b2352877` |
| W06 Runtime/Migration | passed | RUN-001, RUN-002, MIG-001, REL-002 | `7c4b83ad89df51710452dbc7f494113cf45970b2` |
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
9. Tracked engineering evidence uses logical path roles and SHA-256 path
   fingerprints. Exact host-local paths and W00 execution mappings remain only
   in ignored local state.
10. Separate target-boundary, provenance, and autopilot-state gates cover
    reparse containment, Git/evidence binding, and recovery semantics that do
    not belong to the product-neutrality scanner.

## W00 actual checks

- Input verifier: PASS; handoff 49/49 and source 211 members.
- Source temporary-copy pytest: 329 passed.
- Source purity/side-effect/isolation tools: PASS.
- Initial target parity run: 327 passed, 2 failed due the documented control
  plane purity scope collision.
- Focused repair tests: 15 passed; core purity PASS.
- Sealed target full suite: 343 passed in 19.42s on commit
  `0b29525b93b8e203481fca2282b9f4393b2c4a7a` (tree
  `ee2c38d36c1eddb6be2b1857b453bad9a0e79a6a`).
- Target purity, side-effect-free, repository-isolation, target-boundary, and
  W00 provenance checks: PASS.
- Fresh review round 2: three independent reviewers, Critical 0, High 0.
- Source and handoff post-check: exact digests unchanged.

## Open non-blocking gaps

- No source license or license metadata was supplied.
- The host lacks an offline standard build frontend, setuptools, and wheel.
  W01 therefore verified an explicitly classified stdlib PEP 427 fallback;
  this is not represented as verification of the unavailable backend.

## W01 decisions and evidence

1. All 46 Schemas are package resources behind `importlib.resources`; a
   digest-checked manifest records 46 files and 42 artifact registrations.
   The four unregistered files are shared contracts, not missing versions.
2. Public distribution, import, CLI, Schema IDs, and serialized version
   identifiers remain compatibility contracts. No public migration occurred.
3. JSON entry points are explicit for bytes, paths, and mappings. Duplicate
   keys, non-finite or unsupported numbers, malformed UTF-8, invalid RFC 3339,
   missing paths, and resource-limit violations return stable domain errors.
4. Numeric limits cover both parsed token size and fixed-point expansion so a
   compact exponent cannot allocate an unbounded canonical representation.
5. Authorization binds seven material context digests, exact artifacts, the
   entire request envelope, effective configuration, evaluation time, and
   expiry. Generation sheets, dispatch, and reconciliation all revalidate.
6. Legacy documents remain loadable and legacy structural planning order is
   retained, but neither is an execution authorization without current context.
7. The offline wheel fallback binds filename, dist-info, unique identity
   headers, tags, every RECORD digest/size row, and isolated registry loading.
8. Approval authenticity, revocation, and a durable ledger remain assigned to
   W04; runtime token consumption and durable execution remain assigned to W06.

## W01 actual checks

- Final implementation: `2ba74f80b62d5a550bb195d0ea4c0d69f0e72c21`
  (tree `f023d90f7d8791f59741ff7193c4039ca0dc64cd`).
- Focused trust-boundary suite: 150 passed in 10.92s.
- Full suite: 447 passed in 32.79s.
- Manual fallback wheel: 133 members, 215742 bytes, SHA-256
  `530127e0e2f5c93a7368238e328bffc02a3485b06d31609bb8b62082c8e1b5ce`.
- Isolated no-index installation: 46 Schemas and 42 versions loaded; RECORD,
  wheel identity, and metadata verification PASS.
- Standard backend command: environment blocked with `No module named build`;
  no dependency download or installation was attempted.
- Exact W00 parent compatibility audit: 338 passed; five failures are the
  intentionally retired unsafe authorization successes. All 11 legacy
  non-authorizing planning-order cases pass.
- Final fresh architecture, security, and test/packaging reviews: Critical 0,
  High 0, Medium 0 on the exact final implementation commit and tree.
- Source archive and handoff manifest post-checks remain byte-identical.

## Recovery

The initial import snapshot is immutable and Git-bound. After the W00
checkpoint commit exists, ignored local state records its exact hash and a
tracked recovery anchor records the checkpoint commit/tree plus a canonical
semantic state digest. Recovery validates Git ancestry, input digests, state
schema/semantics, and target containment before continuing.

## W02 decisions and evidence

1. Managed mutation is an additive package with six closed artifact contracts,
   strict bytes-only deserialization, a deterministic pure planner, revision and
   drift logic, and narrow runtime protocols. Existing storage/executor public
   protocols remain unchanged.
2. The exact compare-and-swap surface binds workspace ID, canonical parent
   revision digest, before manifest, per-operation expected bytes/path state,
   current complete observation, and atomic idempotency reservation.
3. Requester risk cannot lower policy risk. W02 conservatively assigns every
   mutation R4 until W04 supplies a trusted request-and-policy-bound classifier.
4. R4 requires two distinct authenticated human records bound to the exact
   canonical break-glass request plus current snapshot, incident, audit,
   evaluation, expiry, session, executor, and reconciliation scope.
5. Each unique create/replace content object is resolved once and retained with
   its exact resolver evidence. Plan-aware consumers require complete object-set
   equality before a receipt can promote a trusted workspace revision.
6. Cross-platform path validation rejects non-canonical, aliased, reserved,
   linked, reparsed, escaping, or non-directory-ancestor targets. Drift compares
   complete workspace identity, entries, lengths, bytes, and manifest.
7. A concrete filesystem executor, durable journal/crash recovery, trusted
   authority ledger, and lower-tier classifier remain explicitly assigned to
   W04/W06. W02 does not simulate them or claim their completion.

## W02 actual checks

- Final implementation: `0fa62c81bcd2fa9537aa93db47403a73b388073a`
  (tree `cc7e398710addb03a5febdd86d435a0d9a037aad`).
- Managed-mutation focused suite: 73 passed on the exact implementation commit.
- Full suite: 527 passed on the exact implementation commit/tree.
- Core purity 273, side-effect-free 90, repository isolation 197, and target
  boundary 330 files/59 directories: PASS.
- Deterministic stdlib wheel: two identical 148-member, 262234-byte builds,
  SHA-256 `afb79cc2b46cec41325d592cda22bb5395c87337b60a07423b5fc9dabb6e7cd3`.
- Isolated wheel verification: 52 packaged Schemas and 48 registered versions;
  RECORD, metadata, resource manifest, and imports PASS.
- Standard backend command remains environment-blocked with
  `No module named build`; no package was installed or downloaded.
- Final fresh architecture, authority-boundary, and test/packaging reviews:
  Critical 0, High 0, Medium 0 on the exact final implementation commit/tree.
- Source archive and handoff manifest post-checks remain byte-identical.

## W03 decisions and evidence

1. Channel Constitution, Concept Constitution, EpisodeIntent, and
   ProductionBlueprint are immutable additive contracts. The actual validated
   Channel -> Concept -> EpisodeIntent bundle, episode identity, and eight
   material context digests must agree at every Director consumer.
2. A general builder cannot create a coherent Blueprint. Coherent design is
   derived only by deterministic Director synthesis and becomes a verified
   promotion claim only after its complete source, registry, activation, task,
   assessment, conflict, and antecedent evidence is recomputed.
3. The target-owned Director registry contains twelve core and six conditional
   charters. Production activation, ownership, planning, synthesis, and
   promotion verification reject reduced, augmented, reversioned, or otherwise
   co-tampered registries.
4. Director work is logically parallel over one exact draft Blueprint. There
   are no Director-to-Director workflow predecessors. Conflict synthesis is
   deterministic, hard constraints win first, and only one exact blocked
   predecessor permits a second and final round.
5. Round two recomputes the complete round-one task and assessment evidence.
   Every previous and current task input, execution receipt, top-level evidence,
   patch evidence, and blocker evidence shares one cross-platform path identity
   set. Only an exact `(path, sha256, artifact_version)` triple may be reused.
6. Immutable reference paths are canonical relative POSIX NFC strings and
   reject empty/dot/parent/repeated/trailing segments, platform aliases,
   case/Unicode collisions, and every Unicode `Cc` control character.
7. Legacy views are deterministic read-only Blueprint projections. Comparison
   inputs remain explicitly unverified diagnostic observations until a
   versioned semantic normalizer exists; no W03 output grants readiness,
   dispatch, mutation, publish, release, parity, or cutover authority.
8. W03 defines a Director runtime protocol only. No model/provider call,
   credential use, network access, human approval, publication, or deployment
   occurred.

## W03 actual checks

- Final implementation: `d0b2f5f4607bcfe76c28a0b8e33da2ae3407f02c`
  (tree `5a265eb538b9a681307bdce1bacf011d53eddd5f`).
- W03 target suite: 54 passed; security-focused eight-file suite: 156 passed;
  architecture-focused contracts/compatibility suite: 76 passed.
- Full suite: 584 passed in 75.74s on the exact implementation commit/tree.
- Core purity 313, side-effect-free 103, repository isolation 223, and target
  boundary 378 files/63 directories: PASS.
- Schema resources: 61 packaged schemas and 57 registered versions; root and
  package projections and the digest manifest are exact.
- Target-owned Director resources: 18 charters (12 core, 6 conditional), six
  conditional activation rules, and a two-round maximum; code/resource semantic
  projection checks PASS.
- Deterministic stdlib wheel: two identical 173-member, 311429-byte builds,
  SHA-256 `ae1479bb61300087490876ef12f8fa810a31eb2504941d30f67cc41e5128ccc8`.
- Isolated wheel verification: RECORD, unique metadata, schema manifest,
  Director resources, imports, 61 schemas, and 57 versions PASS.
- Standard backend command remains environment-blocked because `build`,
  `setuptools`, and `wheel` are absent; the fallback is not represented as
  backend verification.
- Final fresh architecture, security/authority, and test/packaging reviews:
  Critical 0, High 0, Medium 0 on the exact final implementation commit/tree.
- Handoff 49/49 files and source archive 211 members/CRC/digest remain exact.

## W04 decisions and evidence

1. The legacy imperative planner and all public action identifiers remain
   available while a separate target-owned declarative workflow evaluates 24
   claims and gates. The actual dual-run corpus covers 26 action seeds in three
   modes and compares action, blocker, actor, required authority, consumed
   evidence, and prohibited actions.
2. Gate results, workflow evaluations, parity reports, and executable plans are
   immutable and non-authorizing. Their exact material context, frontier,
   evidence, executable-plan digest, and ordered incremental predecessor chain
   must be recomputed before an authority request can consume them.
3. Incremental adapter inputs are mode-aware and gate-local. Unchanged helpers
   are actually skipped, changed claims invalidate their exact dependents, and
   the incremental semantic projection must equal a clean recomputation. A
   chain longer than eight predecessors requires a clean rebase.
4. DeclarativeGateRun is an ephemeral cache, not evidence. Its owner-bound seal
   cannot survive replacement or copying; reused claim IDs and GateResults must
   exactly match the previous evaluation's unchanged inputs and results.
5. Routine storyboard review is a non-human integrated preflight. Standard and
   Controlled intentionally consolidate only the legacy `approve_storyboard`
   frontier into `create_generation_packet`. The explanation is target-owned
   and restricted to those exact two rows and all six compared dimensions.
6. Assurance, autonomy, and action risk are independent. The exact request and
   closed hard-escalation facts are classified by the target policy. A known
   material creative deviation becomes supported R4 and requires two
   independent humans; an UNKNOWN fact remains unsupported and denied.
7. Every authority source requires current trusted verification evidence.
   Standing documents are parse-only, self-approval is forbidden, R4 standing
   authority is forbidden, two human signature proofs must be content-distinct,
   and the R4 validity window is at most 300 seconds.
8. Dispatch, reconcile, and mutation obtain a fresh purpose-bound reservation
   receipt. Exact request, context, risk, evaluation chain, scope, budget,
   idempotency, workspace, adapter, service, revocation, and kill-switch facts
   are revalidated immediately before the side effect; unsupported risk stops
   before the ledger port is called.
9. W02 mutation uses the same exact W04 request-envelope and idempotency
   identity and retains its additional R4 break-glass evidence. W04 does not
   implement durable ledger storage, real identity/signature infrastructure,
   provider execution, publication, deployment, or migration cutover.

## W04 actual checks

- Final implementation: `9ff1465c88ce48be70bbb185d8495d860f68cb61`
  (tree `88929617fc1e563cd28f50a5938b8339f8b1a758`).
- Final delta tests: 6 passed; focused W04 suite: 379 passed; integrated
  workflow/authority/orchestration/adapter/mutation suite: 495 passed.
- Actual dual run: 78 passed (26 seeds x 3 modes); the only action differences
  are the two target-owned storyboard process-consolidation rows.
- Compatibility/schema/resource/CLI suite: 138 passed. Full suite: 947 passed
  in 200.34s on the exact implementation tree.
- Core purity 362, side-effect-free 117, repository isolation 255, and target
  boundary 438 files/67 directories: PASS. W00/W01 provenance and W01/W02/W03
  recovery checks remain PASS.
- Schema resources: 73 root/package byte-identical schemas and 68 registered
  versions. Workflow-authority resources: exact definition, policy, parity
  normalization, and three-entry digest manifest.
- Deterministic stdlib wheel: two identical 203-member, 389418-byte builds,
  SHA-256 `25d9f865aefd49670af0f1f73c6e53830a97675dee5166748e20b70df63a6d66`.
- Isolated wheel verification: RECORD, unique metadata, 73/68 schema resources,
  workflow-authority resources, imports, CLI 17, and version 0.3.1 PASS.
- Standard backend command remains environment-blocked because `build`,
  `setuptools`, and `wheel` are absent; the fallback is not represented as
  backend verification.
- Final fresh architecture, security/authority, and test/packaging reviews:
  Critical 0, High 0, Medium 0 on the exact final implementation commit/tree.
- A clean manifest projection verifies all 49 handoff files; the source archive
  remains exact at 211 members and its mandated digest.

## W05 decisions and evidence

1. Automatic candidate choice uses the additive `candidate-decision/1.0`
   contract and dedicated `auto_select_candidates` authority action. Legacy
   ranking remains advisory and the legacy projection is non-current and
   non-authorizing.
2. Selection binds the complete candidate set, exact media, immutable
   confidence receipts, adapter identities, quality, policy, material context,
   and workspace/channel/concept/episode scope. Score, confidence, and margin
   thresholds are integer basis points; ties and every unmet condition produce
   stable escalation reasons.
3. The target QualityBundle covers nine closed dimensions for every exact media
   subject. Each evaluator receipt is independently verified at both the
   externally supplied origin time and current verification time. Hard and
   safety failures cannot be averaged away.
4. Remediation is bounded to two retries and only exact failed
   shot/component/dimension targets. Replay, no progress, regression,
   oscillation, and exhaustion escalate. W05 does not implement a remediation
   executor.
5. ReleaseCandidate binds final media, metadata, subtitles/accessibility,
   thumbnail, quality, candidate decision, destination, policy, material
   context, workspace observation, and exact production scope. Candidate and
   quality lineage is reverified at creation and assessment.
6. ReleaseAssessment requires exactly one current one-shot human under the
   initial policy. Missing approval creates only a non-authorizing request;
   campaign and self approval remain disabled. Ready is a handoff state only:
   publication is false and no publisher exists in W05.
7. Complete invalid authority attempts retain exact non-authorizing audit
   provenance. Partial, malformed, self-rehashed, cross-bound, stale, revoked,
   or semantically rebound evidence is rejected before it can authorize or be
   misrepresented as a different origin.
8. Authority receipt and decision derivation share W04 canonical validators.
   RFC 3339 timestamps are compared as timezone-aware instants, so equivalent
   encodings interoperate while any real time change fails closed.
9. Concrete resolvers, durable authority and attempt ledgers, provider work,
   publication, settlement, credentials, crash recovery, and legacy cutover
   remain explicitly assigned to W06.

## W05 actual checks

- Final implementation: `a241fe300676c0fcd8d31615534c2e728e02dbcb`
  (tree `bec97b6def4fec9a7c0e0316e04b5f4c0ff160cb`).
- Final RFC 3339 delta: 6 passed. Authority and CandidateDecision focused suite:
  187 passed. Full suite: 1004 passed in 618.26s.
- Core purity 408, side-effect-free 135, repository isolation 289, and target
  boundary 493 files/72 directories: PASS. W00/W01 provenance and W01-W04 plus
  generic recovery anchors remain PASS.
- Schema resources: 80 root/package byte-identical schemas and 75 registered
  versions. Quality-release policy resource manifest and semantic validation:
  PASS. Public version `0.3.1`, CLI 17, and prior identifiers are unchanged.
- Deterministic stdlib wheel: two identical 230-member, 442916-byte builds,
  SHA-256 `ce70c7ce6a8eeea6eb4948df5fec8144f670a4a36bbc2413bceba5beeb694076`.
- Isolated wheel verification: RECORD, unique metadata, 80/75 schema resources,
  workflow, Director, quality-release resources, and imports PASS.
- Standard backend remains environment-blocked because `build`, `setuptools`,
  and `wheel` are absent; the fallback is not represented as backend
  verification.
- A first direct handoff post-check correctly rejected scratch verification
  directories. A clean manifest projection then verified all 49 listed files;
  the source archive verified 211 members and its exact mandated digest.

## W06 decisions and evidence

1. The pure `video_factory` package remains side-effect-free. Seven strict
   runtime/migration artifacts live in `video_factory.runtime`; concrete local
   adapters live only in the additive `video_factory_runtime` package. The
   packaged policy fixes fixture-only operation and disables production.
2. The SQLite journal atomically claims action/idempotency scope with exact
   request and intent digests, uses FULL synchronization, preserves an
   append-only hash chain and immutable receipt history, and rejects same-key
   different-request reuse.
3. `planned`, `authorized`, and `reserved` may resume only after every current
   check is repeated. `dispatching`, `dispatched`, `partial`, `reconciling`, and
   `uncertain` are reconcile-only. Exact terminal replay is read-only and does
   not re-attest identity, consult the kill switch, reserve authority, or invoke
   the effect port.
4. The managed fixture executor uses stable no-follow file descriptors and
   operation-level actual-use CAS, containment, byte, link/reparse, authority,
   and journal checks. Partial or ambiguous state becomes durable uncertainty
   and is never restarted from the beginning.
5. The synthetic external executor validates exact output references and
   bytes, request identity, cost/currency, workspace changes, and uncertainty.
   Restart reconciliation recovers the durable external reference without
   redispatch.
6. Publication is separate from W05 readiness. The fake publisher freshly
   verifies candidate, assessment, destination, workspace, service identity,
   kill switch, and a distinct R3 W04 side-effect reservation. Before/after
   workspace verifier records, settlement, uncertainty, and reconciliation are
   durable; production publication remains disabled.
7. Migration is read-only and reversible across legacy-only, dual-read,
   projection-read-only, and rolled-back states. A fixture-pinned exact-byte
   parity receipt and separate current activation/rollback verification are
   mandatory. Every production consumer remains unregistered and projections
   remain non-current and non-authorizing.
8. Deployment, migration, rollback, incident, drift, break-glass, journal
   recovery, and release runbooks preserve exact evidence and name the
   no-redispatch/rollback conditions. Public identifier migration remains
   deferred to T90.
9. The ordered mutation-effect sequence is inside the canonical
   `ExecutionIntent`, not trusted from a mutable journal index. Dispatch claims
   revalidate the execution row and complete event chain in their transaction.
   Success, failure, and reconciliation can be recorded only atomically with a
   bound receipt. Windows fixture mutation keeps the complete ancestor chain
   and exact directory/file handles live, then rejects any directory-path
   rebound before a handle-relative effect.

## W06 completed checks

- The final implementation is commit
  `376cca4e2113e9d730dfbf130fc6ce5216717400`, tree
  `c6644afcc90985b37de422c4cf71a823f6af6486`, with a clean tracked
  worktree. The final repair preserves the complete POSIX directory capability
  chain, separates stable reservations from fresh authority receipts, and
  binds every migration generation to one exact legacy artifact.
- The root final focused run passed 139 tests with four platform-only skips.
  Independent focused reviews passed 102 tests with four skips, 70 tests with
  three POSIX-only skips, and eight direct authority/runtime cases.
- Two independent full runs passed 1086 tests with four platform-only skips
  and zero failures. The root run completed in 916.60 seconds; independent
  acceptance completed in 883.30 seconds.
- Schema/resource projection passed with 87 root/package byte-identical
  schemas, 82 registered versions, schema manifest SHA-256
  `b418fe462b187ee1f4c0d7d7f7a7332203504d3456b0a44f0820e73589075ff1`,
  and runtime/migration resource manifest SHA-256
  `8417a172e2274f17d9d9d38744da554d893c21301fa3807c195d0ddf820733e8`.
- Two clean offline wheel builds were byte-identical: 255 members, 534338
  bytes, SHA-256
  `76635b5340c108299f9fdc2694fbe9128fc9046779a3dc92d8afa9d570702f45`.
  Both source-free isolated probes loaded 87 schemas and 82 versions and
  verified the exact packaged resource manifests.
- Final boundaries passed: core purity 462/0, side-effect-free 140/0, runtime
  dependency direction 140 core + 10 runtime/0, repository isolation 323/0,
  and target boundary 560 files + 77 directories/0. W00/W01 provenance and all
  W01 through W05 recovery checks also passed.
- Final architecture, authority/security, and test/packaging reviewers each
  reported Critical 0, High 0, Medium 0 and `GO`. W06 evidence sealing is the
  only remaining work before the W07 final independent audit.

## W07 decisions and evidence

1. W07 re-runs the original architecture, trust, compatibility, packaging,
   migration, and recovery acceptance rather than treating earlier wave
   receipts as current authority. Four fresh read-only roles cover architecture,
   security/runtime authority, test/packaging, and documentation/migration.
2. Managed mutation accepts only a canonical W02 issuance verified by the
   trusted boundary and binds the complete authorization digest into durable
   execution intent. Cached runtime children retain device/inode identity and
   are rechecked for type, reparse/link status, containment, and identity before
   reopen or effect.
3. Credential handles remain opaque and are bound by the full attestation
   reference, exact service/destination/purpose, current broker verification,
   and durable reconciliation evidence. No raw credential can enter artifacts,
   journal rows, receipts, logs, or test evidence.
4. Migration registry v1.1 separates four registered/schema-backed migratable
   views from two shadow-only views. Initialization validates the exact expected
   version, bytes, strict JSON, and installed schema before persisting the
   generation-zero legacy anchor. Production activation remains disabled.
5. Candidate selection cutover is an additive, pure coordinator. Only a
   currently reverified `AUTO_SELECTED` decision can bypass the legacy human
   selection frontier, and the result carries `authority_effect=none`.
   Escalation, denial, missing evidence, or rollback returns the unchanged
   `select_edit_inputs` path.
6. Mode contracts live in a dependency-root module and public modules retain
   exact re-export identity. The committed package-dependency gate rejects
   aliases, dependency-root upward imports, and every non-trivial package SCC.
7. Packaged authority policy is compared in full with the executable target
   policy in both source tests and the source-free wheel probe. Wheel child
   processes use UTF-8 and tolerant diagnostic decoding while all return-code,
   RECORD, identity, schema, and semantic checks remain fail-closed.
8. Public identifier migration is not performed. T90 remains separately
   deferred, and no network, remote, install, provider, publication, deployment,
   credential, or human-approval/signature side effect is authorized or claimed.

## W07 completed checks

- Final implementation candidate: commit
  `2acd77c0b1b80d6449b38a0e79e859b4cfc9f4e5`, tree
  `72bf1859c25a2e450c892777426c70802cc0daf1`, parent
  `fd858ff22a16bbf0eaffa9d813a160ebda9dcce0`; tracked worktree clean.
- Exact full suite: 1104 passed, four platform-only skips, zero failures in
  855.00 seconds. Focused reviewers additionally passed 264/1 packaging,
  120 documentation/migration, 35+99 architecture, and 16+2 runtime checks.
- Six gates passed with zero violations: core purity 468, side-effect-free 142,
  runtime direction 142 core/10 runtime, repository isolation 327, target
  boundary 572 files/78 directories, and package dependencies 31 packages/93
  edges/zero SCC.
- Root/package schemas are byte-identical at 87 schemas and 82 registered
  versions. All workflow, Director, quality/release, and runtime/migration
  resource projections pass exact digest and semantic checks.
- Two independent wheels are byte-identical: 257 members, 539612 bytes,
  SHA-256
  `2ce7642946fd8db1037a93512a6bf7a591da55444407c52ba00bc53bffb3752c`.
  Both isolated probes pass with 87 schemas, 82 versions, both packages,
  version 0.3.1, all 17 CLI commands, and exact public re-export identities.
- W00/W01 provenance, generic recovery, and W01 through W06 recovery checks
  pass. The source archive and handoff manifest remain exact and unchanged.
- Implementation reviewers report Critical 0, High 0, Medium 0. The W07 final
  result, report, complete command receipt, compatibility/T90 decision, and
  rollback range are sealed by the local completion checkpoint; only the
  ignored-state and recovery-anchor binding follows as a metadata-only commit.
