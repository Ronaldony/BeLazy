# ADR-DIR-001 — Logical parallel Director Mesh

- Status: Accepted for W03 contract and pure synthesis
- Decision owner: System architecture
- Scope: Director registry, activation, tasks, assessments, conflicts

## Context

Adding a sequence of specialist handoffs would increase latency and create new
mutable stages. At the same time, one undifferentiated design pass cannot prove
that narrative, staging, camera, motion, generation, edit, sound, continuity,
quality, and audience concerns received explicit ownership and verification.

## Decision

Directors form a logical parallel mesh over one exact base Blueprint. The
target-owned registry contains twelve core charters and six conditional
specialists for factual, rights, compositing, dialogue/voice,
localization/accessibility, and live-production needs.

Activation is deterministic from a versioned policy and exact EpisodeIntent
digest. Every active task binds the registry, activation policy, charter, base
Blueprint, Blueprint context, and immutable input references. There are no
task-to-task predecessor dependencies.

Each `director-assessment/1.0` binds the exact task, model identifier, prompt
charter version, request and response digests, execution receipt, evidence,
assumptions, confidence, blockers, and structured field patches. Consumers
revalidate all bindings against the current Blueprint before synthesis.

Field ownership is concrete rather than inferred from a broad registry glob:
every active field has exactly one selected owner and one or more distinct
verifiers. An activated conditional specialist must own at least one concrete
field.

Conflicts use deterministic policy with hard constraints first, then the
primary owner, then charter priority. All decisions and rejected proposals are
recorded. Synthesis is limited to two rounds; unresolved conflict, any blocker,
uncertain verdict, or confidence below policy produces a blocked result.

## Runtime boundary

W03 defines `DirectorRuntimePort` only. The core does not call a model, access a
network, select credentials, persist receipts, or synthesize approval evidence.
Runtime adapters must return immutable assessments that the core validates.

## Consequences

- Specialist work can run in parallel against one base digest.
- Mixed, stale, incomplete, or out-of-scope assessments fail closed.
- A larger registry does not create additional human workflow stages.
- Runtime model execution and durable evidence remain deferred.

## Rejected alternatives

- Sequential Director stages: creates avoidable handoffs and stale intermediate
  state.
- Unbounded debate: lacks deterministic termination and recovery.
- Caller-declared ownership: cannot prove exact field coverage.
