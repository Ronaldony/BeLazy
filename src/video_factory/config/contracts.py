"""Public contracts for layered configuration and effective snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Collection, Mapping, Protocol, Sequence

from video_factory.domain import HashDigest, OpaqueId, RelativeArtifactPath
from video_factory._mode_contracts import ExecutionModeLimits


class ConfigLayer(StrEnum):
    """Configuration precedence from least to most authoritative."""

    CORE_DEFAULTS = "core_defaults"
    WORKSPACE = "workspace"
    CHANNEL = "channel"
    CONCEPT = "concept"
    EPISODE = "episode"
    RUNTIME_OVERRIDE = "runtime_override"
    HUMAN_DECISION = "human_decision"


CONFIG_LAYER_ORDER: tuple[ConfigLayer, ...] = (
    ConfigLayer.CORE_DEFAULTS,
    ConfigLayer.WORKSPACE,
    ConfigLayer.CHANNEL,
    ConfigLayer.CONCEPT,
    ConfigLayer.EPISODE,
    ConfigLayer.RUNTIME_OVERRIDE,
    ConfigLayer.HUMAN_DECISION,
)


@dataclass(frozen=True, slots=True)
class ExtensionPayload:
    """Versioned data interpreted only by its registered owner."""

    contract_version: str
    payload: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ConfigSource:
    """One validated layer and the canonical digest of its source document."""

    layer: ConfigLayer
    source_id: OpaqueId
    path: RelativeArtifactPath
    sha256: HashDigest
    values: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """Source metadata retained in a snapshot without duplicating source values."""

    layer: ConfigLayer
    source_id: OpaqueId
    path: RelativeArtifactPath
    sha256: HashDigest


@dataclass(frozen=True, slots=True)
class EffectiveScope:
    workspace_id: OpaqueId
    channel_profile_id: OpaqueId
    concept_profile_id: OpaqueId | None
    episode_id: OpaqueId | None


@dataclass(frozen=True, slots=True)
class EffectiveVersions:
    """Independent distribution, contract, policy, and opaque rule versions."""

    core_distribution: str
    core_contract: str
    rules_version: str
    policy_version: str
    config_contract: str


@dataclass(frozen=True, slots=True)
class EffectiveBindings:
    core_lock_sha256: HashDigest


@dataclass(frozen=True, slots=True)
class ProvenanceEntry:
    winning_layer: ConfigLayer
    source_id: OpaqueId


@dataclass(frozen=True, slots=True)
class SafetyConstraints:
    applied: tuple[str, ...]
    rejected_overrides: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EffectiveConfigSnapshot:
    artifact_version: str
    created_at: str
    scope: EffectiveScope
    versions: EffectiveVersions
    bindings: EffectiveBindings
    sources: tuple[SourceRecord, ...]
    effective: Mapping[str, object]
    provenance: Mapping[str, ProvenanceEntry]
    constraints: SafetyConstraints
    effective_config_sha256: HashDigest


class ExtensionValidator(Protocol):
    """Owner-supplied validation for an opaque extension namespace."""

    def validate(
        self,
        contract_version: str,
        payload: Mapping[str, object],
    ) -> tuple[str, ...]: ...


class ConfigMerger(Protocol):
    """Port for deterministic validation, merge, provenance, and hashing."""

    def merge(
        self,
        sources: Sequence[ConfigSource],
        *,
        scope: EffectiveScope,
        versions: EffectiveVersions,
        core_lock_sha256: HashDigest,
        created_at: str,
        runtime_override_allowlist: Collection[str],
        extension_validators: Mapping[str, ExtensionValidator],
        execution_mode_limits: ExecutionModeLimits | None = None,
    ) -> EffectiveConfigSnapshot: ...
