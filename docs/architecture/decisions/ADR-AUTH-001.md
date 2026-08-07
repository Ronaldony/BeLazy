# ADR-AUTH-001 - Exact action authority and independent control profiles

- Status: Accepted for W04 authority contracts and pure evaluation
- Decision owner: Security and authority architecture
- Scope: risk classification, grants, approval requests, decisions, exact scope

## Context

Legacy approval documents can bind artifact and configuration context, but they
cannot prove a current signature, immutable-ledger inclusion, revocation state,
budget reservation, or kill-switch state. Assurance, autonomy, and risk were
also easy to conflate, allowing a mode or high-confidence AI result to appear
more authoritative than it is.

## Decision

`AssuranceProfile`, `AutonomyProfile`, and `ActionRisk` are independent axes.
Assurance describes evidence rigor, autonomy describes how work may be
proposed, and risk determines required authority. No mode, profile name, AI
review, Director result, or consensus creates authority.

An `ActionAuthorityRequest` binds the exact request-envelope digest and
idempotency key; workflow action, capability, and executable plan; all seven
`GateContext` digests; workspace/channel/concept/episode; provider, model, and
destination; canonical collision-free input and output references;
cost/currency, candidate and retry limits; and requested profiles. Workflow
requests must match a validated target-owned executable plan. The only
non-workflow bridge is exact R4 `managed_mutation`.

The target-owned `authority-policy/2.1` bundle recomputes the effective risk and
contains an enforcement matrix with an owner, phase, reason code, and positive
and negative test identifier for every policy field. Unknown actions are
unsupported and denied as R4 escalation; caller risk can never lower policy.

`standing-authorization/1.0` is explicitly unverified parse-only input. Core has
no grant builder, issuer, signer, or authentication API. R4 standing grants are
invalid. `approval-request/1.0` has `creates_authority=false` and is only a
human-facing request for missing evidence.

Only an `authority-decision/1.0` produced after a trusted ledger verifies the
exact request, context, risk, scope, ledger state, signatures, revocation,
limits, validity, and permitted authority source can carry
`execution_authority`. R2 accepts a scoped standing grant or one current human;
R3 accepts one-shot human or release-campaign authority; R4 requires two
distinct humans and forbids standing authority. Structural IDs and hashes prove
integrity, not authenticity.

Standing scope binds exact input path/hash/version identities and exact output
prefix/version scopes in addition to subject, provider/model, destination,
cost, candidates, retries, profiles, validity, and material context. A content
digest alone cannot transfer authority to a different artifact path or
version. Human-required actions are evaluated from their explicit
`AuthorityRequirement`, even when action risk is R0; risk does not erase an
approval checkpoint.

## Consequences

- Raw approvals and grants remain loadable without becoming permission.
- Material scope and context changes invalidate the decision.
- Human intervention is requested only where policy requires it.
- Real identity, signature, and ledger implementations remain external trusted
  services behind ADR-AUTH-002 ports.

## Rejected alternatives

- Treat legacy `ApprovalEvidence` as a signed grant: it lacks authenticity and
  revocation semantics.
- Let callers declare action risk: permits authority downgrade.
- Let assurance or autonomy imply authority: mixes quality and permission.
