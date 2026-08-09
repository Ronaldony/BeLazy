# ADR-AUTH-002 - Fresh ledger revalidation at every side-effect boundary

- Status: Accepted for W04 trusted ports and guards; durable runtime deferred to W06
- Decision owner: Runtime security architecture
- Scope: dispatch, reconcile, managed mutation, reservations, invalidation

## Context

An authority decision can become stale after initial evaluation because a
grant is revoked, a ledger is superseded, a kill switch changes, a budget is
consumed, the workspace drifts, or another request reserves the same
idempotency key. Passing a previously computed boolean or receipt to an adapter
does not close that time-of-check/time-of-use gap.

## Decision

`TrustedAuthorizationLedger` has two distinct operations. `verify_current`
supports initial decision evaluation. `revalidate_and_reserve_current` is
called directly by the immediate side-effect guard and atomically rechecks
current authority and reserves current limits.

The returned `authority-verification-receipt/1.0` is bound to one purpose:
`initial_decision`, `dispatch`, `reconcile`, or `mutation`. It binds the exact
request and decision digests, seven-digest context, risk assessment, workspace
observation, adapter and service identity, active ledger head/entry, verified
signatures and principals, revocation check time, kill-switch state, cost and
candidate reservation, retry index, currency, idempotency key, and validity
window. Workflow receipts also bind the exact clean workflow-evaluation digest
and one immutable trusted verification reference; mutation receipts require
both fields to be null. A receipt from one purpose cannot be replayed for
another.

Initial receipts deliberately have no workspace-observation or adapter/service
binding. Dispatch, reconcile, and mutation receipts must carry the exact
current workspace-observation SHA-256 plus adapter and service identity.
Non-policy receipts require signature-verification references, and a
standing-source receipt must bind the exact standing-grant digest. The core
rechecks those fields, the authenticated requester, and the exact current risk
assessment after the trusted ledger returns and before handing
authority to a side-effect boundary.

An authorized decision seals an `authority_basis_sha256`: the exact authority
source, grant identity when applicable, ledger entry, each
principal-to-signature pair, authenticated requester, and workflow-evaluation
verification proof. Fresh revalidation may advance the ledger head, but it
cannot silently replace the initial human, grant, requester, workflow proof,
signature evidence, or authority source.

Executor dispatch and reconcile require both the legacy structural
`OrchestrationAuthorization` and the exact W04 request/decision, but the legacy
object has `authority_effect=none`. The adapter calls the trusted ledger after
all exact request/context/workspace checks and before entering its external
method. The immutable `ExecutorAuthorityScope` received by that method binds
the actual workspace/channel/concept/episode, selected adapter/provider,
model, destination, cost/currency, candidate count, and retry index to the W04
request. Reconcile reuses the scope stored with the unresolved dispatch.
Missing/unavailable ledger, stale or denied decision, expired window,
rebound receipt, revocation, kill switch, or reservation mismatch yields zero
external calls.

Managed mutation performs the same fresh `mutation`-purpose revalidation. The
generic W04 R4 dual-human decision is additive to the W02 exact plan-aware
authorization, content observation, idempotency reservation, and break-glass
snapshot/incident/audit evidence. Neither layer substitutes for the other.

W04 defines and tests the strict port semantics with deterministic fakes. W06
owns concrete persistent ledgers, signature verification, trusted clocks,
atomic budget settlement, executor and journal persistence, crash recovery,
and filesystem/network TOCTOU handling.

## Consequences

- Revocation and kill-switch changes are observed immediately before effects.
- Dispatch and reconcile consume separate current proofs.
- Tests can prove `external_calls == 0` for every denied or rebound condition.
- The pure core remains free of network, process, filesystem mutation, and
  credential access.

## Rejected alternatives

- Reuse the initial receipt: misses current revocation, budgets, and kill switch.
- Trust a caller-supplied `authorized=true`: provides no ledger or scope proof.
- Implement a concrete ledger in core: violates the runtime boundary and W06
  sequencing.
