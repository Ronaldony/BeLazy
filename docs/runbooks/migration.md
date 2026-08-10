# Read-only projection migration runbook

## Safety boundary

The packaged migration registry lists every production consumer as
`unregistered`, and production activation is disabled. W03 diagnostic shadow
observations are not parity evidence. W06 can exercise only an isolated fixture
consumer backed by an immutable pinned exact-byte parity corpus.

## Procedure

1. Verify exact legacy bytes and the strict registered legacy artifact schema.
2. Verify exact `blueprint-projection/1.0` bytes, source Blueprint digest,
   compiler identity, consumer ID, and view kind.
3. Obtain a `projection-parity-receipt/1.0` from the trusted verifier. An unseen
   pair fails closed.
4. Initialize `legacy_only` with the exact immutable legacy artifact reference
   and a separate current activation record. That reference is permanent for
   the entire migration chain.
5. Enter `dual_read_compare`; continue serving the legacy reference.
6. Enter `projection_read_only` only with a fresh exact parity receipt and a
   separate current activation record for that state generation.
7. Select only a read reference. The projection remains non-current,
   non-editable, and `authority_effect=none`.
8. On rollback, select the state-bound legacy reference only. Do not supply an
   older parity receipt as a rollback target; projection rollback audit refs are
   copied from the immediately preceding activation, while dual-read rollback
   needs no parity evidence.

## Stop conditions

Stop before transition when parity is missing/stale/rebound, approval is
missing, the feature-flag or policy digest changed, the state chain is stale,
or a consumer/view is not explicitly allowed. Never use a PASS label or AI
review as activation approval.

## Required evidence

Preserve legacy and projection references, their exact bytes, parity receipt
and verifier record, policy/feature-flag digests, activation record, state
generation/hash, and the prior state hash.
