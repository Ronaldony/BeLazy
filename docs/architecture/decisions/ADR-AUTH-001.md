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

An `ActionAuthorityRequest` binds the exact request-envelope digest,
idempotency key, and requester principal; workflow action, capability,
executable plan, and exact clean workflow evaluation; all seven
`GateContext` digests; workspace/channel/concept/episode; provider, model, and
destination; canonical collision-free input and output references;
cost/currency, candidate and retry limits; requested profiles; and the closed
target hard-escalation catalog with exact evidence for every fact. Workflow
requests must match a validated target-owned executable plan. The only
non-workflow bridge is exact R4 `managed_mutation`.

The target-owned `authority-policy/2.1` bundle binds the exact governance YAML
digest, self-approval prohibition, disabled release-campaign activation, and
complete hard-escalation trigger catalog. It recomputes the effective risk,
sets the R4 receipt lifetime ceiling to 300 seconds, and
contains an enforcement matrix with an owner, phase, reason code, and positive
and negative test identifier for every policy field. Each row identifies a
distinct executable parameterized test node that exercises the real positive
and negative boundary and checks the observed reason code. Unknown actions are
unsupported and denied as R4 escalation; caller risk can never lower policy.
Triggered or unknown hard-escalation facts are unsupported R4 escalation;
missing, reordered, or evidence-free facts are invalid requests.

`standing-authorization/1.0` is explicitly unverified parse-only input. Core has
no grant builder, issuer, signer, or authentication API. R4 standing grants are
invalid. `approval-request/1.0` has `creates_authority=false` and is only a
human-facing request for missing evidence.

Only an `authority-decision/1.0` produced after a trusted ledger verifies the
exact request, context, risk, scope, ledger state, signatures, revocation,
limits, validity, permitted authority source, authenticated requester, and (for
workflow actions) exact clean evaluation plus immutable verification proof can carry
`execution_authority`. The same trusted evaluation proof is mandatory for
policy-owned R0/R1 workflow actions; POLICY is not a receipt-free authority
shortcut. R2 accepts a scoped standing grant or one current human.
R3 accepts one current human in W04; release-campaign authority remains
fail-closed until later trusted campaign/content-risk facts exist. R4 requires
two distinct humans and forbids standing authority. Every human principal is
bound one-to-one to a signature-verification reference whose content digest is
distinct from every other authentication proof. The requester has a separate
authentication reference and cannot be an approver. Structural IDs and hashes prove
integrity, not authenticity. Ledger entries, requester authentication,
signature verification, and workflow evaluation verification use distinct
exact artifact versions. A generic bound artifact with the wrong role cannot
be substituted even when it is self-hashed.

Standing scope binds exact input path/hash/version identities and exact output
prefix/version scopes in addition to subject, provider/model, destination,
cost, candidates, retries, profiles, validity, and material context. A content
digest alone cannot transfer authority to a different artifact path or
version. Human-required actions are evaluated from their explicit
`AuthorityRequirement`, even when action risk is R0; risk does not erase an
approval checkpoint.
Input identities, output prefixes, grant signature references, and principal
verification pairs have one canonical order.

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
