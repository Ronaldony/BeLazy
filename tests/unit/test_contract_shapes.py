from __future__ import annotations

from dataclasses import fields, is_dataclass

from video_factory.blueprint import (
    BlueprintProjection,
    BlueprintSourceBundle,
    ProductionBlueprint,
    UnverifiedShadowObservation,
)
from video_factory.config import (
    CONFIG_LAYER_ORDER,
    ChannelConfig,
    ConceptConfig,
    ConfigLayer,
    EffectiveConfigSnapshot,
    EpisodeConfig,
    WorkspaceConfig,
)
from video_factory.providers import (
    CapabilityDescriptor,
    ExecutorAdapter,
    ProviderAdapter,
    RequestEnvelope,
    ResultEnvelope,
)
from video_factory.directors import (
    DirectorAssessment,
    DirectorRuntimePort,
    DirectorTaskPlan,
    VerifiedBlueprintPromotion,
)


def _field_names(contract: type[object]) -> tuple[str, ...]:
    return tuple(field.name for field in fields(contract))


def test_config_layer_order_and_models_are_explicit() -> None:
    assert CONFIG_LAYER_ORDER == (
        ConfigLayer.CORE_DEFAULTS,
        ConfigLayer.WORKSPACE,
        ConfigLayer.CHANNEL,
        ConfigLayer.CONCEPT,
        ConfigLayer.EPISODE,
        ConfigLayer.RUNTIME_OVERRIDE,
        ConfigLayer.HUMAN_DECISION,
    )
    assert all(
        is_dataclass(model)
        for model in (WorkspaceConfig, ChannelConfig, ConceptConfig, EpisodeConfig)
    )
    assert _field_names(EffectiveConfigSnapshot) == (
        "artifact_version",
        "created_at",
        "scope",
        "versions",
        "bindings",
        "sources",
        "effective",
        "provenance",
        "constraints",
        "effective_config_sha256",
    )


def test_shared_adapter_descriptor_and_envelopes_have_required_shape() -> None:
    assert _field_names(CapabilityDescriptor) == (
        "adapter_id",
        "adapter_kind",
        "contract_version",
        "capabilities",
        "supported_execution_modes",
        "input_artifact_versions",
        "output_artifact_versions",
        "side_effects",
        "uncertainty_model",
        "constraint_profiles",
    )
    assert _field_names(RequestEnvelope) == (
        "request_id",
        "capability_id",
        "effective_execution_mode",
        "effective_config_sha256",
        "input_artifacts",
        "allowed_outputs",
        "idempotency_key",
        "creator_role",
        "reviewer_role",
    )
    assert _field_names(ResultEnvelope) == (
        "request_id",
        "outcome",
        "outputs",
        "external_reference",
        "measured_cost",
        "uncertainty",
    )


def test_provider_and_executor_ports_are_behaviorally_separate() -> None:
    assert "plan_generation" in ProviderAdapter.__dict__
    assert "prepare_human_handoff" in ProviderAdapter.__dict__
    assert "dispatch" not in ProviderAdapter.__dict__
    assert "dispatch" in ExecutorAdapter.__dict__
    assert "reconcile" in ExecutorAdapter.__dict__
    assert "plan_generation" not in ExecutorAdapter.__dict__
    assert "execute" not in ProviderAdapter.__dict__
    assert "execute" not in ExecutorAdapter.__dict__


def test_blueprint_and_director_contract_shapes_are_explicit() -> None:
    assert _field_names(BlueprintSourceBundle) == (
        "channel_constitution",
        "concept_constitution",
        "episode_intent",
    )
    assert _field_names(ProductionBlueprint) == (
        "blueprint_id",
        "blueprint_sha256",
        "episode_id",
        "revision",
        "status",
        "context",
        "fields",
        "ownership",
        "director_provenance",
        "unresolved_blockers",
    )
    assert _field_names(BlueprintProjection) == (
        "projection_id",
        "projection_sha256",
        "source_blueprint_id",
        "source_blueprint_sha256",
        "source_blueprint_ref",
        "blueprint_context_sha256",
        "compiler_id",
        "compiler_version",
        "compiler_sha256",
        "view_kind",
        "legacy_artifact_version",
        "payload_sha256",
        "shadow_only",
        "authority_effect",
        "editable",
        "read_only",
        "fields",
    )
    assert _field_names(UnverifiedShadowObservation) == (
        "observation_id",
        "observation_sha256",
        "legacy_artifact_ref",
        "legacy_document_sha256",
        "trust_state",
        "diagnostic_only",
        "authority_effect",
        "observed_view_sha256",
        "fields",
    )
    assert _field_names(VerifiedBlueprintPromotion) == (
        "blueprint",
        "synthesis",
        "activation_sha256",
        "authority_effect",
    )
    assert _field_names(DirectorTaskPlan) == (
        "task_id",
        "task_sha256",
        "director_id",
        "director_version",
        "charter_sha256",
        "registry_sha256",
        "activation_id",
        "activation_sha256",
        "activation_policy_sha256",
        "episode_intent_sha256",
        "base_blueprint_sha256",
        "blueprint_context_sha256",
        "input_refs",
        "owned_fields",
        "verified_fields",
    )
    assert "activation_sha256" in _field_names(DirectorAssessment)
    assert "execution_receipt" in _field_names(DirectorAssessment)
    assert "assess" in DirectorRuntimePort.__dict__
    assert "execute" not in DirectorRuntimePort.__dict__
