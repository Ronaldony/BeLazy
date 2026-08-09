# W04 Discovery - Declarative Workflow and Authority Control

## Subject

- Wave: `W04-WORKFLOW-AUTHORITY`
- Tasks: `WF-001`, `AUTH-001`, `AUTH-002`, `WF-002`
- Approved W03 recovery ancestor: `86cb95572f4302116a5e8267021139cce43fd345`
- Final implementation candidate: `9ff1465c88ce48be70bbb185d8495d860f68cb61`
- Final implementation tree: `88929617fc1e563cd28f50a5938b8339f8b1a758`

## Workflow discovery

- The legacy imperative planner remains public and unchanged while a separate
  target-owned declarative plane evaluates 24 claims and gates over the same
  episode observation. All 26 legacy action identities and three workflow
  modes are characterized by an actual 78-row dual run.
- Gate results, workflow evaluations, and executable plans are non-authorizing
  immutable documents. Their material context, consumed evidence, frontier,
  executable-plan digest, and ordered incremental predecessor chain must agree
  before any authority request can consume them.
- Incremental evaluation uses mode-aware, gate-local adapter input digests.
  Unchanged evaluators are actually skipped, changed claims are transitively
  invalidated, and every incremental semantic projection must equal a clean
  recomputation. The ordered chain is limited to eight predecessors before a
  clean rebase is required.
- Ephemeral gate-run caches are accepted only from the target builder. A cache
  seal cannot survive replacement, shallow copy, or deep copy, and every reused
  claim and GateResult must agree with the exact previous evaluation.

## Authority discovery

- Assurance profile, autonomy profile, and action risk are independent. The
  target policy classifies the exact action request and closed hard-escalation
  facts; caller risk cannot lower the effective tier.
- Authority decisions require current trusted verification receipts. Standing
  documents are parse-only, R4 cannot use standing authority, self-approval is
  forbidden, and R4 requires two content-distinct authenticated human proofs
  within a 300-second validity window.
- Every side-effect boundary obtains a fresh purpose-bound receipt for
  dispatch, reconcile, or mutation. Exact request, GateContext, risk, workflow
  evaluation chain, scope, budget, idempotency, workspace, adapter, service,
  ledger state, revocation, and kill-switch facts are revalidated immediately
  before reservation. Unsupported current risk stops before the ledger call.
- W02 mutation consumes the same exact W04 request-envelope and idempotency
  identity while retaining its additive R4 break-glass requirements.

## Process improvement and parity

- Routine storyboard review is an integrated non-human preflight. It does not
  create a human checkpoint; Standard and Controlled proceed directly from the
  legacy `approve_storyboard` point to `create_generation_packet`.
- Only those two rows differ from the legacy action frontier. The target-owned
  process-consolidation explanation is restricted to the exact row and exact
  action, blocker, actor, authority, and evidence values; it cannot explain
  unrelated or unmapped behavior.
- A known material creative deviation is classified as supported R4 and
  requires two independent humans. An UNKNOWN classifier fact remains
  unsupported and denied at both initial and pre-side-effect evaluation.

## Selected scope and deferrals

- W04 implements pure contracts, deterministic evaluation, strict serializers,
  packaged policy/workflow/parity resources, trusted-port protocols, and strict
  fakes. It does not synthesize human approval or ledger evidence.
- Concrete durable ledger storage, real identity/signature infrastructure,
  provider execution, crash recovery, settlement, publication, deployment,
  and migration cutover remain assigned to W06 or later waves.

