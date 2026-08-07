"""Immutable contracts for the Production Blueprint shadow plane."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from video_factory.domain import ArtifactReference, HashDigest, OpaqueId


class BlueprintStatus(StrEnum):
    DRAFT = "draft"
    COHERENT = "coherent"
    BLOCKED = "blocked"


class BlueprintProjectionKind(StrEnum):
    BRIEF = "brief"
    STORYBOARD = "storyboard"
    GENERATION = "generation"
    EDIT = "edit"
    SOUND = "sound"
    PUBLISH = "publish"


@dataclass(frozen=True, slots=True)
class BlueprintField:
    """One active field stored as bounded canonical JSON text."""

    path: str
    value_json: str


@dataclass(frozen=True, slots=True)
class BlueprintContext:
    channel_constitution_sha256: HashDigest
    concept_constitution_sha256: HashDigest
    episode_intent_sha256: HashDigest
    policy_bundle_sha256: HashDigest
    rules_bundle_sha256: HashDigest
    effective_config_sha256: HashDigest
    current_manifest_sha256: HashDigest
    evidence_graph_sha256: HashDigest


@dataclass(frozen=True, slots=True)
class FieldOwnership:
    field_path: str
    owner_director_id: OpaqueId
    verifier_director_ids: tuple[OpaqueId, ...]


@dataclass(frozen=True, slots=True)
class DirectorProvenance:
    director_id: OpaqueId
    director_version: str
    charter_sha256: HashDigest
    assessment_sha256: HashDigest
    input_blueprint_sha256: HashDigest
    blueprint_context_sha256: HashDigest


@dataclass(frozen=True, slots=True)
class ChannelConstitution:
    constitution_id: OpaqueId
    constitution_sha256: HashDigest
    channel_id: OpaqueId
    rules_version: str
    fields: tuple[BlueprintField, ...]


@dataclass(frozen=True, slots=True)
class ConceptConstitution:
    constitution_id: OpaqueId
    constitution_sha256: HashDigest
    concept_id: OpaqueId
    channel_constitution_sha256: HashDigest
    rules_version: str
    fields: tuple[BlueprintField, ...]


@dataclass(frozen=True, slots=True)
class EpisodeIntent:
    intent_id: OpaqueId
    intent_sha256: HashDigest
    episode_id: OpaqueId
    channel_constitution_sha256: HashDigest
    concept_constitution_sha256: HashDigest
    needs_complexity: str
    activation_signals: tuple[str, ...]
    fields: tuple[BlueprintField, ...]


@dataclass(frozen=True, slots=True)
class BlueprintSourceBundle:
    """Validated source artifacts used to interpret a Blueprint context."""

    channel_constitution: ChannelConstitution
    concept_constitution: ConceptConstitution
    episode_intent: EpisodeIntent


@dataclass(frozen=True, slots=True)
class ProductionBlueprint:
    blueprint_id: OpaqueId
    blueprint_sha256: HashDigest
    episode_id: OpaqueId
    revision: int
    status: BlueprintStatus
    context: BlueprintContext
    fields: tuple[BlueprintField, ...]
    ownership: tuple[FieldOwnership, ...]
    director_provenance: tuple[DirectorProvenance, ...]
    unresolved_blockers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BlueprintProjection:
    projection_id: OpaqueId
    projection_sha256: HashDigest
    source_blueprint_id: OpaqueId
    source_blueprint_sha256: HashDigest
    source_blueprint_ref: ArtifactReference
    blueprint_context_sha256: HashDigest
    compiler_id: OpaqueId
    compiler_version: str
    compiler_sha256: HashDigest
    view_kind: BlueprintProjectionKind
    legacy_artifact_version: str
    payload_sha256: HashDigest
    shadow_only: bool
    authority_effect: str
    editable: bool
    read_only: bool
    fields: tuple[BlueprintField, ...]


@dataclass(frozen=True, slots=True)
class ShadowFieldDifference:
    field_path: str
    projected_value_sha256: HashDigest | None
    observed_value_sha256: HashDigest | None


@dataclass(frozen=True, slots=True)
class UnverifiedShadowObservation:
    observation_id: OpaqueId
    observation_sha256: HashDigest
    legacy_artifact_ref: ArtifactReference
    legacy_document_sha256: HashDigest
    trust_state: str
    diagnostic_only: bool
    authority_effect: str
    observed_view_sha256: HashDigest
    fields: tuple[BlueprintField, ...]


@dataclass(frozen=True, slots=True)
class ShadowComparison:
    comparison_id: OpaqueId
    comparison_sha256: HashDigest
    legacy_artifact_ref: ArtifactReference
    projection_ref: ArtifactReference
    projection_sha256: HashDigest
    observation_sha256: HashDigest
    observed_view_sha256: HashDigest
    comparator_id: OpaqueId
    comparator_version: str
    comparator_sha256: HashDigest
    comparison_semantics: str
    authority_effect: str
    differences: tuple[ShadowFieldDifference, ...]

    @property
    def diagnostic_equal(self) -> bool:
        return not self.differences
