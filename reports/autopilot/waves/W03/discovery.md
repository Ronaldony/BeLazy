# W03 Discovery — Director Mesh and ProductionBlueprint Shadow

## Subject

- Wave: `W03-DIRECTOR-BLUEPRINT`
- Tasks: `BP-001`, `DIR-001`, `DIR-002`, `BP-002`
- Approved W02 ancestor: `789756c24c9e501bb7f1708694638884b7459338`
- Final implementation: `c48ef226e8abb226a6bbfe679972154fc9cd9f55`
- Final implementation tree: `89103954b160bc26c50781a231ce30030f4c5f13`

## Contract discovery

- The W02 recovery seal had no Channel Constitution, Concept Constitution,
  EpisodeIntent, ProductionBlueprint, Director registry, assessment, synthesis,
  conflict, or shadow-projection contract. W03 adds these as immutable additive
  artifacts without changing the existing public distribution, import, CLI, or
  artifact identifiers.
- `production-blueprint/1.0` carries the complete episode design, per-shot
  design, material context, exact field ownership, distinct verification,
  immutable reference locks, Director provenance, blockers, and status.
- The exact Channel -> Concept -> EpisodeIntent source chain is validated as an
  object bundle. Episode identity, source digests, Blueprint context, and all
  promotion evidence must agree; digest strings alone are not trusted.
- A general builder can create only draft or blocked Blueprints. Coherent state
  is derived through Director synthesis and is considered verified only after
  the complete promotion evidence is recomputed.

## Director discovery

- The target-owned registry contains twelve core Directors and six conditional
  specialists. Production activation, ownership, task planning, synthesis, and
  promotion verification require that exact registry and current activation
  policy; a caller-provided reduced or replaced registry cannot become
  authoritative by remaining internally self-consistent.
- Directors operate logically in parallel over one exact draft Blueprint.
  Their tasks bind the registry, activation policy, charter, source bundle,
  base Blueprint, context, and immutable exact-byte references.
- Assessments bind the exact task plus model, prompt charter, request, response,
  execution receipt, evidence, assumptions, confidence, blockers, and
  structured field patches. PASS is design evidence, never human approval or
  execution authority.
- Conflict handling is deterministic and limited to two rounds. Round two must
  carry and recompute the complete blocked round-one task and assessment
  evidence. Persisted coherent artifacts cannot start a new W03 promotion
  chain.

## Shadow and path discovery

- Blueprint projections are deterministic read-only legacy views. They remain
  non-current in the ArtifactGraph and cannot authorize readiness, generation,
  provider dispatch, mutation, publication, or release.
- Until a versioned semantic legacy normalizer exists, comparison records are
  explicitly unverified diagnostic observations. W03 does not claim parity or
  cutover from caller-supplied normalized fields.
- All Blueprint locks, Director inputs, projections, and observations share one
  canonical NFC relative-POSIX path rule. Empty, dot, parent, repeated,
  trailing, absolute, drive, backslash, case/Unicode alias, and every Unicode
  `Cc` control character fail closed.

## Selected scope

- W03 implements immutable contracts, strict serializers, pure deterministic
  activation/planning/synthesis/projection logic, resource projections, and a
  runtime protocol only.
- Actual model calls, credentials, network access, durable invocation receipts,
  human approval, workflow authority, cutover, provider publication, and
  deployment remain outside W03.

