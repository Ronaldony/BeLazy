# Runtime and migration rollback runbook

## Runtime rollback

1. Engage the trusted kill switch and stop new idempotency claims.
2. Snapshot the fixture root and SQLite files without altering them.
3. Classify every journal entry.
4. `planned`, `authorized`, and `reserved` may resume or be cancelled only
   after proving no dispatch marker and rerunning every current check.
5. `dispatching`, `dispatched`, `partial`, `reconciling`, and `uncertain` are
   reconcile-only. Never dispatch them again.
6. Terminal exact requests return their existing receipt read-only.

## Migration rollback

Request an exact transition to `rolled_back`, bound to the current state hash,
new feature-flag digest, policy digest, generation, and a separately verified
rollback record. The selected source becomes legacy. A stale pre-rollback state
must not remain selectable.

## Evidence

Preserve the last verified wheel, runtime marker, journal database and event
chain, terminal and historical receipts, settlement/reconciliation records,
migration history, rollback record, and all exact artifact references.

Rollback does not delete evidence, undo an already-started external effect, or
grant permission to edit managed files manually.
