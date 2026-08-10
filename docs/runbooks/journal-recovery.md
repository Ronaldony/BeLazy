# Durable journal recovery runbook

## Integrity first

Open only the marker-bound fixture root. Validate the SQLite schema version,
intent identity, scope key, request digest, intent-bound ordered effect IDs,
every event self-hash, sequence, previous-event hash, row head, current state,
current receipt, and immutable receipt history. Repeat the row and chain checks
inside the atomic dispatch claim rather than relying on an earlier load. Any
corruption quarantines the journal.

Every may-have-started state is reconcile-only; it never returns to dispatch.

## State handling

| State | Recovery action |
|---|---|
| `planned`, `authorized`, `reserved` | No dispatch marker exists. Reacquire the lease, rerun current workspace/identity/kill-switch/authority checks, then resume or cancel. |
| `dispatching`, `dispatched` | Effect may have started. Never redispatch; invoke reconciliation only. |
| `partial` | Preserve per-operation results and reconcile remaining observed state; never restart the plan. |
| `reconciling`, `uncertain` | Continue reconciliation with a fresh reconcile-purpose reservation. |
| `succeeded`, `failed`, `reconciled` | Terminal. Return the exact durable receipt read-only for an exact idempotent replay. |

Never use the generic state transition API to declare success, failure, or
reconciliation. Those states are valid only when the event and exact bound
receipt were committed together by the journal's atomic finish operation.

## Idempotency

The scope is `(action kind, action id, idempotency key)`. A different request
digest or intent under that scope is a hard conflict. Exact terminal replay
does not re-attest identity, recheck the kill switch, reserve authority, or
invoke an executor/publisher/mutator.

Before any trusted reservation call, confirm that the stable reservation claim
is durable. A restart must reuse that exact claim rather than charge a second
reservation. Enumerate every claim with a ledger result but no settlement;
reconciliation settles the complete set against one final state. For mutation,
verify that each dispatch marker names the exact next operation in the durable
ordered effect plan and that the redundant journal index is exactly equal to
the sequence embedded in the canonical `ExecutionIntent`.

## Evidence

Record journal ID, intent SHA-256, event-chain head, all historical receipt
digests, external reference if present, workspace before/after observations,
authority receipt/reservation, settlement, and reconciliation record.
