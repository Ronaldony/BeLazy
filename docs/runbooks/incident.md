# Runtime incident runbook

## Immediate response

1. Engage the kill switch.
2. Stop new effects, but keep journal and fixture bytes intact.
3. Record the runtime ID, service identity, wheel digest, current time, journal
   ID, intent digest, event-chain head, and workspace observation.
4. Treat every effect after a persisted `dispatching` marker as possibly
   started.

## Triage

- Idempotency mismatch: reject both requests and investigate the caller.
- Hash-chain, receipt, or marker corruption: quarantine the fixture; do not
  repair bytes in place.
- Timeout/process death/ambiguous result: mark or retain `uncertain` and use
  only the action-specific reconcile port.
- Workspace drift or TOCTOU failure: quarantine workspace trust and follow the
  drift runbook.
- Missing settlement: hold the reservation and retain `uncertain`.
- Credential exposure: this runtime should contain only opaque handles. If a
  secret appears, stop and treat it as a separate security incident.

## Recovery rule

Reconciliation requires fresh purpose=`reconcile` W04 evidence, current service
identity, current kill-switch clearance, and exact durable intent binding. A
reconciliation failure never authorizes redispatch.
