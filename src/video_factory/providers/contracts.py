"""Shared adapter envelopes with separate provider and executor behavior ports."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Mapping, Protocol

from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    CapabilityId,
    HashDigest,
    IdempotencyKey,
    OpaqueId,
    RelativeArtifactPath,
    RequestId,
    RoleId,
)
from video_factory._mode_contracts import ExecutionMode


class AdapterKind(StrEnum):
    PROVIDER = "provider"
    EXECUTOR = "executor"


class SideEffect(StrEnum):
    NONE = "none"
    LOCAL_WRITE = "local_write"
    EXTERNAL_CALL = "external_call"
    QUOTA_CONSUMING = "quota_consuming"
    DATA_UPLOAD = "data_upload"


class Outcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    EXTERNAL_UNCERTAIN = "EXTERNAL_UNCERTAIN"
    AWAITING_HUMAN = "AWAITING_HUMAN"


class FirstFrameAspectBehavior(StrEnum):
    """How a generation capability treats image-to-video source dimensions."""

    MATCH_OUTPUT = "match_output"
    FREE = "free"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class UncertaintyModel:
    timeout_state_is_confirmable: bool
    reconcile_supported: bool


@dataclass(frozen=True, slots=True)
class CapabilityConstraintProfile:
    """Profile-supplied generation limits; values are never core constants."""

    profile_id: OpaqueId
    profile_sha256: HashDigest
    capability_id: CapabilityId
    minimum_duration_seconds: Decimal | None
    first_frame_aspect_behavior: FirstFrameAspectBehavior
    unsupported_render_dependencies: frozenset[OpaqueId]


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    adapter_id: OpaqueId
    adapter_kind: AdapterKind
    contract_version: str
    capabilities: frozenset[CapabilityId]
    supported_execution_modes: frozenset[ExecutionMode]
    input_artifact_versions: frozenset[ArtifactVersion]
    output_artifact_versions: frozenset[ArtifactVersion]
    side_effects: frozenset[SideEffect]
    uncertainty_model: UncertaintyModel
    constraint_profiles: tuple[CapabilityConstraintProfile, ...] = ()


@dataclass(frozen=True, slots=True)
class AllowedOutput:
    path_prefix: RelativeArtifactPath
    artifact_versions: frozenset[ArtifactVersion]


@dataclass(frozen=True, slots=True)
class RequestEnvelope:
    request_id: RequestId
    capability_id: CapabilityId
    effective_execution_mode: ExecutionMode
    effective_config_sha256: HashDigest
    input_artifacts: tuple[ArtifactReference, ...]
    allowed_outputs: tuple[AllowedOutput, ...]
    idempotency_key: IdempotencyKey
    creator_role: RoleId
    reviewer_role: RoleId


@dataclass(frozen=True, slots=True)
class CostMeasurement:
    amount: Decimal | None
    unit: str | None
    is_unknown: bool


@dataclass(frozen=True, slots=True)
class UncertaintyEvidence:
    uncertain: bool
    reason: str | None
    reconcile_evidence: ArtifactReference | None


@dataclass(frozen=True, slots=True)
class ExternalReference:
    """Opaque external request and session identifiers, when emitted."""

    request_id: str | None
    session_id: str | None


@dataclass(frozen=True, slots=True)
class ResultEnvelope:
    request_id: RequestId
    outcome: Outcome
    outputs: tuple[ArtifactReference, ...]
    external_reference: ExternalReference | None
    measured_cost: CostMeasurement
    uncertainty: UncertaintyEvidence


@dataclass(frozen=True, slots=True)
class ValidationReport:
    accepted: bool
    reasons: tuple[str, ...]
    derived_values: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ProviderPlan:
    request_id: RequestId
    candidate_count: int
    cost_unit: str
    estimated_cost: Decimal | None
    reservation_reference: OpaqueId | None
    expected_outputs: tuple[RelativeArtifactPath, ...]


@dataclass(frozen=True, slots=True)
class ProviderPreview:
    """Local-only validation and derived values for preview mode."""

    result: ResultEnvelope
    validation: ValidationReport
    derived_values: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class HumanHandoff:
    """Instructions and expected names prepared without an external call."""

    steps: tuple[str, ...]
    expected_outputs: tuple[RelativeArtifactPath, ...]


@dataclass(frozen=True, slots=True)
class ProviderHumanResult:
    result: ResultEnvelope
    handoff: HumanHandoff | None
    plan: ProviderPlan | None


@dataclass(frozen=True, slots=True)
class ReadOnlyStaging:
    """Exact immutable inputs and output contract exposed to an executor."""

    input_artifacts: tuple[ArtifactReference, ...]
    allowed_outputs: tuple[AllowedOutput, ...]
    read_only: bool


@dataclass(frozen=True, slots=True)
class ExecutorAuthorityScope:
    """Runtime facts that the executor must use for one external action.

    These values are deliberately separate from the caller's authority
    document.  The immediate dispatch guard compares them with the exact W04
    request, and the executor receives the same immutable object.
    """

    workspace_id: OpaqueId
    channel_id: OpaqueId
    concept_id: OpaqueId
    episode_id: OpaqueId
    provider_id: OpaqueId
    model_id: OpaqueId
    destination: str
    cost_minor_units: int
    currency: str
    candidate_count: int
    retry_index: int


@dataclass(frozen=True, slots=True)
class ExecutorDispatchContext:
    staging: ReadOnlyStaging
    requested_tools: frozenset[OpaqueId]
    allowed_tools: frozenset[OpaqueId]
    capability_allowlist: frozenset[CapabilityId]
    authority_scope: ExecutorAuthorityScope | None = None


@dataclass(frozen=True, slots=True)
class NormalizedEvent:
    """Adapter-neutral event produced from one concrete stream event."""

    event_type: OpaqueId
    payload: Mapping[str, object]
    external_reference: ExternalReference | None = None


class DescribedAdapter(Protocol):
    @property
    def descriptor(self) -> CapabilityDescriptor: ...


class CapabilityRegistry(Protocol):
    def describe(
        self,
        capability_id: CapabilityId,
        adapter_kind: AdapterKind,
    ) -> CapabilityDescriptor: ...

    def resolve(
        self,
        capability_id: CapabilityId,
        adapter_kind: AdapterKind,
    ) -> DescribedAdapter: ...
