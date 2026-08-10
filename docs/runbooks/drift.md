# Managed workspace drift runbook

## Detection

Compare the complete observed workspace identity, revision, manifest, entry
paths, node kinds, byte lengths, and SHA-256 values with the last trusted
WorkspaceRevision. Missing ancestors, links, reparse points, special nodes,
case/Unicode aliases, or unknown containment are drift.

## Response

1. Mark the workspace `UNTRUSTED` and block generation, publication, mutation,
   and downstream QC authority.
2. Preserve both the expected revision and current observation.
3. Generate a deterministic DriftReport with all invalidations and blockers.
4. Do not copy, delete, rename, or repair files manually.
5. Reconcile through an explicit baseline or managed-mutation path with trusted
   evidence. A human statement that bytes are current is not evidence.

## Effect-window drift

If drift is detected after a dispatch marker, the journal result is
`partial` or `uncertain`, not a clean failure. Preserve per-operation events and
use reconciliation; never rerun the plan from the beginning.

## Required evidence

Expected revision digest, observed workspace digest, manifest values, exact
differences, detector version, observation time, journal IDs, authority
reservations, and reconciliation result.
