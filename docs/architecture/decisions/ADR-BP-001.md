# ADR-BP-001 — ProductionBlueprint as the design source of truth

- Status: Accepted for W03 shadow mode
- Decision owner: System architecture
- Scope: episode design, Director inputs, derived legacy views

## Context

The legacy workflow stores design intent across a brief, storyboard,
generation packet, edit manifest, sound plan, and release metadata. Independent
editing of those documents permits camera, motion, continuity, sound, and
acceptance intent to diverge before execution.

## Decision

`production-blueprint/1.0` is the single immutable source of truth for episode
design. It records material context digests, detailed active fields, exactly one
owner and at least one distinct verifier for every field, Director provenance,
and unresolved blockers.

The contract includes root design sections and a complete per-shot design:
purpose, timing, state, subjects, environment, camera, performance and motion,
physics, light and color, synchronized sound, transition, generation strategy,
continuity, and acceptance criteria. Missing required detail, duplicate fields,
incomplete ownership, self-verification, stale identity, and non-canonical JSON
fail closed.

Channel and concept constitutions plus `episode-intent/1.0` are immutable inputs
to the Blueprint context. Planning and synthesis receive the actual validated
three-artifact `BlueprintSourceBundle`, not caller-provided digest strings alone.
The bundle must form one exact Channel -> Concept -> EpisodeIntent chain, and the
Intent episode ID, Blueprint top-level episode ID, and `identity.episode_id`
field must agree. A synthesized Blueprint is a new revision; it never modifies
its base object in place.

`coherent` is a verified promotion state. Every required field must carry a
non-empty value of its declared structural kind, reference locks must contain
safe relative paths plus exact byte digests and artifact versions, and Director
provenance must exactly cover every active owner and verifier. Incomplete design
objects remain `draft` or `blocked`.

The general Blueprint builder cannot create `coherent`. Director synthesis
derives the promoted object only after validating the complete source bundle,
registry, activation, tasks, charters, assessments, base Blueprint, and context.
A deserialized coherent artifact is structurally loadable for observation, but
its promotion claim is trusted only after the same evidence is recomputed by
`verify_coherent_blueprint_promotion`. Reference-lock lists use canonical path
order, forbid duplicates/collisions, and cannot bind one path to different byte
digests or artifact versions across root and per-shot fields. Paths must already
be canonical relative POSIX NFC values; empty, `.`, `..`, repeated or trailing
segments and case/Unicode aliases fail closed.

Logical identity digests (`blueprint_sha256`, `projection_sha256`) and immutable
artifact byte digests have different meanings. `ArtifactReference.sha256`
always hashes the exact canonical serialized bytes. The logical digest remains
a separate field and is never substituted for the byte digest.

## Authority boundary

A coherent Blueprint is a design result, not action authority. It cannot
authorize generation, provider dispatch, workspace mutation, or release. Those
boundaries continue to require their existing current context, workspace trust,
human evidence, and later authority decisions.

## Consequences

- One logical digest represents the complete design and its material context;
  a separate exact-byte digest addresses serialized artifact bytes.
- Field-level provenance and ownership can be audited independently.
- More detail is required before a Blueprint can be coherent.
- Legacy consumers continue operating during W03 through non-authoritative
  projections; no cutover occurs in this decision.
- Until a versioned legacy semantic normalizer exists, shadow observations are
  explicitly unverified diagnostics. Field equality is not parity evidence.

## Rejected alternatives

- Keep each legacy artifact independently editable: permits design drift.
- Treat a Director PASS as approval: conflates design review with authority.
- Store free-form Director prose only: cannot bind patches or verify coverage.
