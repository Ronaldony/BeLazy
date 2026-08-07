# W02 Review Round 3

## Reviewed snapshot

- Commit: `56170d0865cb8153fd53e94598cb99e4ea7db3d4`
- Tree: `b8742f6962c32543aa7b4d026bfef10e963da009`
- Parent: `ebbc3fbf031080a31abf4375e7423a545c0b31db`
- Verdict: `CHANGES_REQUESTED`

## Result

The successful authority-boundary review reported Critical 0, High 1, Medium
0. With two distinct create/replace content objects, an authorization could
retain only one resolver evidence item and still satisfy the then-independent
object/evidence set digest plus permissive evidence-count rule. A matching
receipt could therefore promote a revision without evidence for every unique
content object.

The architecture and acceptance reviewer responses were rejected by the
platform output filter. They are recorded as review errors, not approvals.

## Repair

Commit `0fa62c81bcd2fa9537aa93db47403a73b388073a` stores a canonical
`ContentObjectObservation` for each unique object in the execution
authorization, includes each object-to-evidence pair in authorization identity,
and requires the observed object set to equal the exact plan object set at every
plan-aware consumer. The partial-evidence case is a committed negative test.
