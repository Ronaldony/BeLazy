# ADR-BOOT-001 — Create `be-lazy` as a separate successor repository

- Status: Proposed
- Decision owner: System architecture
- Scope: repository topology, bootstrap, provenance, and migration

## Context

The redesign should be implemented in a new repository named `be-lazy`. The existing `video-production-core` repository contains valuable characterization tests, schemas, and a pure planning core, but it must remain unchanged. Treating the request as an in-place rename would mix source preservation, new product identity, public package compatibility, and external hosted-repository administration.

## Decision

Create `be-lazy` as an independent successor repository with fresh Git history and a new remote. Keep `video-production-core` as a read-only source baseline.

Use a controlled snapshot import by default:

- pin the source commit or archive SHA-256;
- classify every imported path;
- exclude source Git metadata, remotes, credentials, caches, build output, virtual environments, and runtime artifacts;
- preserve source licenses and provenance;
- compare source and target characterization tests;
- perform all fixes and redesign only in `be-lazy`.

The initial target may retain legacy distribution, import, CLI, schema, artifact, and serialized identifiers for semantic parity. Their later migration requires a separate compatibility ADR.

Humans do not manually copy or edit repository files in the normal bootstrap. They create an empty repository, invoke the agent, review diffs and reports, and decide whether to merge.

## Consequences

- The existing repository and its users are not disturbed.
- The new design can evolve on its own release cadence and governance model.
- Provenance and test parity become explicit rather than implied by a rename.
- Two repositories may temporarily expose the same legacy Python distribution identity; publishing and installation coexistence must therefore remain disabled until a package-identity migration is decided.
- Hosted repository creation, remote configuration, push, and merge remain separate administrative actions.

## Rejected alternatives

- In-place rename: contradicts the desired separate-repository topology and risks public contract confusion.
- Blind full-tree copy: imports secrets, caches, Git metadata, and undocumented behavior.
- Immediate clean-room rewrite: discards useful tests and increases semantic regression risk.
- Automatic full-history mirror: imports historical secrets and external side effects without an explicit governance decision.
