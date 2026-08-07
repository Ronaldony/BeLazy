# W03 — Director Mesh and ProductionBlueprint shadow

## Task IDs

`BP-001`, `DIR-001`, `DIR-002`, `BP-002`

## Objective

Create a detailed single source of truth for video design and a logical mesh of professional directors without adding sequential human or agent handoff stages.

## Required contracts

- Channel Constitution
- Concept Constitution
- EpisodeIntent
- ProductionBlueprint
- DirectorCharter
- DirectorAssessment
- DirectorSynthesis/ConflictResult
- read-only Blueprint projections

## Required detail

Blueprint must represent narrative intent, audience/success criteria, visual language, locked references/assets, shot graph, camera/lens/framing/movement/focus, blocking/gaze/performance, motion physics, lighting/color, sound/foley/sync/mix, edit rhythm/transitions, generation capability/prompt/reference/candidate/retry/fallback, continuity state and acceptance criteria.

## Director model

- Executive, story, staging, visual, cinematography, motion/performance, generation technical, editing/rhythm, sound, continuity, quality and audience/distribution ownership.
- Conditional specialist activation for factual, rights, VFX, dialogue/voice, localization/accessibility and live production needs.
- Explicit field owner, verifier, evidence, assumptions, confidence, blocker and patch.
- Bounded conflict resolution with safety/authority first; no unbounded agent debate.

## Shadow mode

- Generate legacy brief/storyboard/generation/edit/sound/publish views from the Blueprint.
- Projections are derived read-only views, not independently edited sources.
- Do not cut over legacy authoritative artifacts yet.
- Actual model calls remain runtime ports/fakes.

## Acceptance

- Every active Blueprint field has owner and verifier coverage.
- Ownership conflict, missing coverage, confidence and blocker cases are tested.
- Projection generation is deterministic.
- Legacy compatibility and shadow comparison are documented.
