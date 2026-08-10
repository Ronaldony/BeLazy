# W06 Review Round 1 - Initial Runtime Candidate

## Reviewed snapshot

- Commit: `a36c0a5424c3d7b8506d246db8ef52dd46cb93c3`
- Tree: `58f020ba9fd7e4ceee84a0038c45ece8b90ba941`
- Parent: `9cca6ff6bb0955c9fa7dafd0a1daf46a4a42ce10`
- Verdict: `CHANGES_REQUESTED`

## Consolidated findings

The initial candidate established the additive durable journal, fixture
runtime, publisher, migration registry, schemas, resources, and runbooks.
Review found that POSIX mutation still performed path-resolved effects after
opening ancestor handles, and that stable reservation identity was incorrectly
equated with a fresh timestamp-bound W04 receipt. Review also found that
migration rollback did not persist one exact legacy anchor across every state,
could consume a historical activation receipt, and that the deployment
runbook's wheel-verification command omitted required CLI flags. No Critical
finding was recorded, but unresolved High and Medium findings required repair.

