from __future__ import annotations

from dataclasses import fields, is_dataclass

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
