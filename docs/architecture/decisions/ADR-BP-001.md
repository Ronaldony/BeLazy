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
to the Blueprint context. A synthesized Blueprint is a new revision; it never
modifies its base object in place.

## Authority boundary

A coherent Blueprint is a design result, not action authority. It cannot
authorize generation, provider dispatch, workspace mutation, or release. Those
boundaries continue to require their existing current context, workspace trust,
human evidence, and later authority decisions.

## Consequences

- One canonical digest represents the complete design and its material context.
- Field-level provenance and ownership can be audited independently.
- More detail is required before a Blueprint can be coherent.
- Legacy consumers continue operating during W03 through non-authoritative
  projections; no cutover occurs in this decision.

## Rejected alternatives

- Keep each legacy artifact independently editable: permits design drift.
- Treat a Director PASS as approval: conflates design review with authority.
- Store free-form Director prose only: cannot bind patches or verify coverage.
