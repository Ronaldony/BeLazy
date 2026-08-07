"""Immutable contracts for the logical parallel Director Mesh."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from video_factory.blueprint.contracts import ProductionBlueprint
from video_factory.domain import ArtifactReference, HashDigest, OpaqueId


class DirectorKind(StrEnum):
    CORE = "core"
    CONDITIONAL = "conditional"


class NeedsComplexity(StrEnum):
    LIGHT = "LIGHT"
    PRODUCTION = "PRODUCTION"
    COMPLEX = "COMPLEX"


class DirectorVerdict(StrEnum):
    PASS = "pass"
    PATCH = "patch"
    BLOCKED = "blocked"
    UNCERTAIN = "uncertain"


class ProposalKind(StrEnum):
    OWNER = "owner"
    VERIFIER = "verifier"


class ConflictStatus(StrEnum):
    RESOLVED = "resolved"
    BLOCKED = "blocked"


class SynthesisStatus(StrEnum):
    COHERENT = "coherent"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class EpisodeNeedsProfile:
    complexity: NeedsComplexity
    activation_signals: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DirectorActivation:
    activation_id: OpaqueId
    activation_sha256: HashDigest
    registry_sha256: HashDigest
    activation_policy_sha256: HashDigest
    episode_intent_sha256: HashDigest
    complexity: NeedsComplexity
    activation_signals: tuple[str, ...]
    active_director_ids: tuple[OpaqueId, ...]


@dataclass(frozen=True, slots=True)
class DirectorCharter:
    charter_id: OpaqueId
    charter_sha256: HashDigest
    director_id: OpaqueId
    director_version: str
    kind: DirectorKind
    owned_patterns: tuple[str, ...]
    verified_patterns: tuple[str, ...]
    activation_signals: tuple[str, ...]
    veto_patterns: tuple[str, ...]
    conflict_priority: int
    rules_version: str


@dataclass(frozen=True, slots=True)
class DirectorTaskPlan:
    task_id: OpaqueId
    task_sha256: HashDigest
    director_id: OpaqueId
    director_version: str
    charter_sha256: HashDigest
    registry_sha256: HashDigest
    activation_id: OpaqueId
    activation_sha256: HashDigest
    activation_policy_sha256: HashDigest
    episode_intent_sha256: HashDigest
    base_blueprint_sha256: HashDigest
    blueprint_context_sha256: HashDigest
    input_refs: tuple[ArtifactReference, ...]
    owned_fields: tuple[str, ...]
    verified_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PatchProposal:
    field_path: str
    proposal_kind: ProposalKind
    expected_value_sha256: HashDigest
    replacement_value_json: str
    reason_code: str
    hard_constraint: bool
    evidence_refs: tuple[ArtifactReference, ...]


@dataclass(frozen=True, slots=True)
class DirectorBlocker:
    reason_code: str
    field_path: str
    hard: bool
    evidence_refs: tuple[ArtifactReference, ...]


@dataclass(frozen=True, slots=True)
class DirectorAssessment:
    assessment_id: OpaqueId
    assessment_sha256: HashDigest
    task_id: OpaqueId
    task_plan_sha256: HashDigest
    director_id: OpaqueId
    director_version: str
    charter_sha256: HashDigest
    registry_sha256: HashDigest
    activation_id: OpaqueId
    activation_sha256: HashDigest
    activation_policy_sha256: HashDigest
    episode_intent_sha256: HashDigest
    base_blueprint_sha256: HashDigest
    blueprint_context_sha256: HashDigest
    model_id: OpaqueId
    prompt_charter_version: str
    request_sha256: HashDigest
    response_sha256: HashDigest
    execution_receipt: ArtifactReference
    verdict: DirectorVerdict
    patches: tuple[PatchProposal, ...]
    blockers: tuple[DirectorBlocker, ...]
    recommendations: tuple[str, ...]
    confidence_basis_points: int
    assumptions: tuple[str, ...]
    evidence_refs: tuple[ArtifactReference, ...]


@dataclass(frozen=True, slots=True)
class ConflictResult:
    conflict_id: OpaqueId
    conflict_sha256: HashDigest
    base_blueprint_sha256: HashDigest
    blueprint_context_sha256: HashDigest
    field_path: str
    proposal_assessment_ids: tuple[OpaqueId, ...]
    status: ConflictStatus
    selected_assessment_id: OpaqueId | None
    rejected_assessment_ids: tuple[OpaqueId, ...]
    selected_replacement_sha256: HashDigest | None
    reason_code: str
    rounds_used: int
    evidence_refs: tuple[ArtifactReference, ...]


@dataclass(frozen=True, slots=True)
class DirectorSynthesis:
    synthesis_id: OpaqueId
    synthesis_sha256: HashDigest
    base_blueprint_sha256: HashDigest
    blueprint_context_sha256: HashDigest
    assessment_sha256s: tuple[HashDigest, ...]
    conflict_sha256s: tuple[HashDigest, ...]
    status: SynthesisStatus
    resulting_blueprint_sha256: HashDigest | None
    unresolved_blockers: tuple[str, ...]
    rounds_used: int


@dataclass(frozen=True, slots=True)
class SynthesisOutcome:
    synthesis: DirectorSynthesis
    blueprint: ProductionBlueprint | None
    conflicts: tuple[ConflictResult, ...]
