"""Shared safe behavior for media providers and structured-task executors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import PurePosixPath
import re
from typing import final

from video_factory.approvals import GateContext
from video_factory.domain import ArtifactReference, IdempotencyKey
from video_factory.engine.contracts import ExecutionMode

from .contracts import (
    AdapterKind,
    CapabilityDescriptor,
    CapabilityConstraintProfile,
    CostMeasurement,
    ExternalReference,
    ExecutorDispatchContext,
    FirstFrameAspectBehavior,
    HumanHandoff,
    NormalizedEvent,
    Outcome,
    ProviderHumanResult,
    ProviderPlan,
    ProviderPreview,
    RequestEnvelope,
    ResultEnvelope,
    SideEffect,
    UncertaintyEvidence,
    ValidationReport,
)
from .enforcement import OrchestrationAuthorization, enforce_adapter_dispatch


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONTRACT_VERSION = re.compile(r"^[1-9][0-9]*\.[0-9]+$")


class AdapterContractError(ValueError):
    """Raised when a descriptor, envelope, staging area, or result is unsafe."""


class ExternalStateUncertain(RuntimeError):
    """Transport failure whose external success state cannot yet be determined."""

    def __init__(
        self,
        reason: str,
        external_reference: ExternalReference | None = None,
    ) -> None:
        super().__init__(reason)
        self.external_reference = external_reference


@dataclass(frozen=True, slots=True)
class _UnresolvedDispatch:
    request: RequestEnvelope
    external_reference: ExternalReference | None


def unknown_cost() -> CostMeasurement:
    return CostMeasurement(amount=None, unit=None, is_unknown=True)


def settled_uncertainty() -> UncertaintyEvidence:
    return UncertaintyEvidence(uncertain=False, reason=None, reconcile_evidence=None)


def _relative_path(value: str, label: str) -> None:
    path = PurePosixPath(value)
    if not value or "\\" in value or path.is_absolute() or ".." in path.parts:
        raise AdapterContractError(f"{label} must be a contained relative POSIX path")


def validate_descriptor(descriptor: CapabilityDescriptor) -> None:
    """Validate the eight shared descriptor fields before binding or use."""

    if not str(descriptor.adapter_id):
        raise AdapterContractError("adapter_id must be opaque and non-empty")
    if _CONTRACT_VERSION.fullmatch(descriptor.contract_version) is None:
        raise AdapterContractError("contract_version must be major.minor")
    if not descriptor.capabilities:
        raise AdapterContractError("descriptor must declare at least one capability")
    if not descriptor.supported_execution_modes:
        raise AdapterContractError("descriptor must declare at least one execution mode")
    if SideEffect.NONE in descriptor.side_effects and len(descriptor.side_effects) != 1:
        raise AdapterContractError("side effect 'none' cannot be combined with other effects")
    if (
        descriptor.adapter_kind is AdapterKind.PROVIDER
        and ExecutionMode.AUTOMATED in descriptor.supported_execution_modes
    ):
        raise AdapterContractError(
            "media-provider descriptors cannot enable automated execution in this contract"
        )
    seen_profile_ids: set[str] = set()
    seen_capabilities: set[str] = set()
    for profile in descriptor.constraint_profiles:
        _validate_constraint_profile(profile, descriptor)
        profile_id = str(profile.profile_id)
        capability_id = str(profile.capability_id)
        if profile_id in seen_profile_ids:
            raise AdapterContractError("constraint profile ids must be unique")
        if capability_id in seen_capabilities:
            raise AdapterContractError(
                "a descriptor may declare at most one constraint profile per capability"
            )
        seen_profile_ids.add(profile_id)
        seen_capabilities.add(capability_id)


def _validate_constraint_profile(
    profile: CapabilityConstraintProfile,
    descriptor: CapabilityDescriptor,
) -> None:
    if not str(profile.profile_id):
        raise AdapterContractError("constraint profile_id must be non-empty")
    if _SHA256.fullmatch(str(profile.profile_sha256)) is None:
        raise AdapterContractError("constraint profile digest must be SHA-256")
    if profile.capability_id not in descriptor.capabilities:
        raise AdapterContractError(
            "constraint profile capability must be declared by the descriptor"
        )
    if (
        profile.minimum_duration_seconds is not None
        and profile.minimum_duration_seconds <= 0
    ):
        raise AdapterContractError(
            "constraint profile minimum duration must be positive when present"
        )
    if not isinstance(
        profile.first_frame_aspect_behavior, FirstFrameAspectBehavior
    ):
        raise AdapterContractError(
            "constraint profile first-frame aspect behavior is invalid"
        )
    if any(not str(item) for item in profile.unsupported_render_dependencies):
        raise AdapterContractError(
            "unsupported render dependency ids must be non-empty"
        )


def validate_request_envelope(
    request: RequestEnvelope,
    descriptor: CapabilityDescriptor,
    expected_kind: AdapterKind,
) -> None:
    validate_descriptor(descriptor)
    if descriptor.adapter_kind is not expected_kind:
        raise AdapterContractError("adapter kind does not match the requested behavior port")
    if request.capability_id not in descriptor.capabilities:
        raise AdapterContractError("capability is not declared by the adapter")
    if request.effective_execution_mode not in descriptor.supported_execution_modes:
        raise AdapterContractError("effective execution mode is not supported by the adapter")
    if not str(request.request_id) or not str(request.idempotency_key):
        raise AdapterContractError("request and idempotency identifiers must be non-empty")
    if _SHA256.fullmatch(request.effective_config_sha256) is None:
        raise AdapterContractError("effective-config digest must be SHA-256")
    if not str(request.creator_role) or not str(request.reviewer_role):
        raise AdapterContractError("creator and reviewer roles must be non-empty")
    if expected_kind is AdapterKind.EXECUTOR and request.creator_role == request.reviewer_role:
        raise AdapterContractError("executor creator and reviewer roles must differ")

    for artifact in request.input_artifacts:
        _relative_path(str(artifact.path), "input artifact path")
        if _SHA256.fullmatch(artifact.sha256) is None:
            raise AdapterContractError("input artifact digest must be SHA-256")
        if artifact.artifact_version not in descriptor.input_artifact_versions:
            raise AdapterContractError("input artifact version is not supported")
    for allowed in request.allowed_outputs:
        _relative_path(str(allowed.path_prefix), "allowed output prefix")
        if not allowed.artifact_versions:
            raise AdapterContractError("allowed output must declare an artifact version")
        if not allowed.artifact_versions <= descriptor.output_artifact_versions:
            raise AdapterContractError("allowed output version is not supported")


def _result(
    request: RequestEnvelope,
    outcome: Outcome,
    *,
    outputs: tuple[ArtifactReference, ...] = (),
    external_reference: ExternalReference | None = None,
    measured_cost: CostMeasurement | None = None,
    uncertainty: UncertaintyEvidence | None = None,
) -> ResultEnvelope:
    return ResultEnvelope(
        request_id=request.request_id,
        outcome=outcome,
        outputs=outputs,
        external_reference=external_reference,
        measured_cost=measured_cost or unknown_cost(),
        uncertainty=uncertainty or settled_uncertainty(),
    )


class ProviderAdapter(ABC):
    """Media planning, local preview, human handoff, and provenance ingest only."""

    @property
    @abstractmethod
    def descriptor(self) -> CapabilityDescriptor: ...

    @abstractmethod
    def validate(
        self,
        request: RequestEnvelope,
        reference_media: Sequence[ArtifactReference],
    ) -> ValidationReport: ...

    @abstractmethod
    def estimate(self, request: RequestEnvelope) -> ProviderPlan: ...

    @abstractmethod
    def derive_preview(
        self,
        request: RequestEnvelope,
        validation: ValidationReport,
    ) -> Mapping[str, object]: ...

    @abstractmethod
    def build_human_handoff(
        self,
        request: RequestEnvelope,
        plan: ProviderPlan,
    ) -> HumanHandoff: ...

    @abstractmethod
    def ingest(
        self,
        request: RequestEnvelope,
        outputs: Sequence[ArtifactReference],
        measured_cost: CostMeasurement,
        provenance: Mapping[str, object],
    ) -> ResultEnvelope: ...

    @final
    def validate_request(
        self,
        request: RequestEnvelope,
        reference_media: Sequence[ArtifactReference],
    ) -> ValidationReport:
        validate_request_envelope(request, self.descriptor, AdapterKind.PROVIDER)
        return self.validate(request, reference_media)

    @final
    def plan_generation(self, request: RequestEnvelope) -> ProviderPlan:
        validate_request_envelope(request, self.descriptor, AdapterKind.PROVIDER)
        return self.estimate(request)

    @final
    def preview_only(
        self,
        request: RequestEnvelope,
        reference_media: Sequence[ArtifactReference] = (),
    ) -> ProviderPreview:
        if request.effective_execution_mode is not ExecutionMode.PREVIEW_ONLY:
            raise AdapterContractError("preview_only requires preview_only effective mode")
        validation = self.validate_request(request, reference_media)
        derived = self.derive_preview(request, validation) if validation.accepted else {}
        outcome = Outcome.SUCCEEDED if validation.accepted else Outcome.REJECTED
        return ProviderPreview(_result(request, outcome), validation, derived)

    @final
    def human_only(
        self,
        request: RequestEnvelope,
        reference_media: Sequence[ArtifactReference] = (),
    ) -> ProviderHumanResult:
        if request.effective_execution_mode is not ExecutionMode.HUMAN_ONLY:
            raise AdapterContractError("human_only requires human_only effective mode")
        validation = self.validate_request(request, reference_media)
        if not validation.accepted:
            return ProviderHumanResult(_result(request, Outcome.REJECTED), None, None)

        plan = self.estimate(request)
        handoff = self.build_human_handoff(request, plan)
        if not handoff.steps:
            raise AdapterContractError("human handoff must contain at least one step")
        if handoff.expected_outputs != plan.expected_outputs:
            raise AdapterContractError("handoff output names must match the estimate")
        return ProviderHumanResult(
            _result(request, Outcome.AWAITING_HUMAN),
            handoff,
            plan,
        )

    @final
    def prepare_human_handoff(
        self,
        request: RequestEnvelope,
        plan: ProviderPlan,
    ) -> ResultEnvelope:
        if request.effective_execution_mode is not ExecutionMode.HUMAN_ONLY:
            raise AdapterContractError("provider dispatch entry point rejects non-human mode")
        validate_request_envelope(request, self.descriptor, AdapterKind.PROVIDER)
        handoff = self.build_human_handoff(request, plan)
        if not handoff.steps or handoff.expected_outputs != plan.expected_outputs:
            raise AdapterContractError("invalid human handoff")
        return _result(request, Outcome.AWAITING_HUMAN)

    @final
    def ingest_human_result(
        self,
        request: RequestEnvelope,
        outputs: Sequence[ArtifactReference],
        measured_cost: CostMeasurement,
        provenance: Mapping[str, object] | None = None,
    ) -> ResultEnvelope:
        validate_request_envelope(request, self.descriptor, AdapterKind.PROVIDER)
        return self.ingest(request, outputs, measured_cost, provenance or {})


class ExecutorAdapter(ABC):
    """Read-only, allowlisted executor dispatch with uncertain-state locking."""

    def __init__(self) -> None:
        self._unresolved: dict[IdempotencyKey, _UnresolvedDispatch] = {}

    @property
    @abstractmethod
    def descriptor(self) -> CapabilityDescriptor: ...

    @abstractmethod
    def _dispatch_stream(
        self,
        request: RequestEnvelope,
        context: ExecutorDispatchContext,
    ) -> Iterable[object]: ...

    @abstractmethod
    def normalize_event(self, event: object) -> NormalizedEvent: ...

    @abstractmethod
    def result_from_events(
        self,
        request: RequestEnvelope,
        events: tuple[NormalizedEvent, ...],
    ) -> ResultEnvelope: ...

    @abstractmethod
    def _reconcile_external(
        self,
        request: RequestEnvelope,
        external_reference: ExternalReference | None,
    ) -> ResultEnvelope: ...

    def _validate_context(
        self,
        request: RequestEnvelope,
        context: ExecutorDispatchContext,
    ) -> None:
        if not context.staging.read_only:
            raise AdapterContractError("executor staging must be read-only")
        if context.staging.input_artifacts != request.input_artifacts:
            raise AdapterContractError("staged input hashes do not match the request")
        if context.staging.allowed_outputs != request.allowed_outputs:
            raise AdapterContractError("staged output contract does not match the request")
        if request.capability_id not in context.capability_allowlist:
            raise AdapterContractError("executor capability is not allowlisted")
        if not context.requested_tools <= context.allowed_tools:
            raise AdapterContractError("executor requested a tool outside the allowlist")

    @final
    def dispatch(
        self,
        request: RequestEnvelope,
        context: ExecutorDispatchContext,
        *,
        authorization: OrchestrationAuthorization | None = None,
        current_context: GateContext | None = None,
        evaluated_at: datetime | None = None,
    ) -> ResultEnvelope:
        if request.idempotency_key in self._unresolved:
            raise AdapterContractError("reconcile is required before executor redispatch")
        validate_request_envelope(request, self.descriptor, AdapterKind.EXECUTOR)
        self._validate_context(request, context)
        enforce_adapter_dispatch(
            request,
            self.descriptor,
            AdapterKind.EXECUTOR,
            authorization=authorization,
            current_context=current_context,
            evaluated_at=evaluated_at,
        )

        try:
            normalized = tuple(
                self.normalize_event(event) for event in self._dispatch_stream(request, context)
            )
        except ExternalStateUncertain as error:
            self._unresolved[request.idempotency_key] = _UnresolvedDispatch(
                request, error.external_reference
            )
            return _result(
                request,
                Outcome.EXTERNAL_UNCERTAIN,
                external_reference=error.external_reference,
                uncertainty=UncertaintyEvidence(True, str(error), None),
            )
        except (TimeoutError, ConnectionError) as error:
            self._unresolved[request.idempotency_key] = _UnresolvedDispatch(
                request, None
            )
            return _result(
                request,
                Outcome.EXTERNAL_UNCERTAIN,
                uncertainty=UncertaintyEvidence(True, type(error).__name__, None),
            )

        result = self.result_from_events(request, normalized)
        if result.request_id != request.request_id:
            raise AdapterContractError("executor result is bound to another request")
        if result.outcome is Outcome.EXTERNAL_UNCERTAIN:
            self._unresolved[request.idempotency_key] = _UnresolvedDispatch(
                request, result.external_reference
            )
        return result

    @final
    def observe(self, request: RequestEnvelope) -> ResultEnvelope:
        unresolved = self._unresolved.get(request.idempotency_key)
        if unresolved is None or unresolved.request != request:
            return _result(request, Outcome.REJECTED)
        return _result(
            request,
            Outcome.EXTERNAL_UNCERTAIN,
            external_reference=unresolved.external_reference,
            uncertainty=UncertaintyEvidence(True, "reconcile required", None),
        )

    @final
    def reconcile(
        self,
        request: RequestEnvelope,
        external_reference: ExternalReference | None = None,
        *,
        authorization: OrchestrationAuthorization | None = None,
        current_context: GateContext | None = None,
        evaluated_at: datetime | None = None,
    ) -> ResultEnvelope:
        unresolved = self._unresolved.get(request.idempotency_key)
        if unresolved is None:
            raise AdapterContractError("request has no unresolved external state")
        if unresolved.request != request:
            raise AdapterContractError(
                "unresolved external state is bound to another request"
            )
        if (
            unresolved.external_reference is not None
            and external_reference is not None
            and external_reference != unresolved.external_reference
        ):
            raise AdapterContractError(
                "reconcile reference does not match unresolved external state"
            )
        reference = (
            external_reference
            if external_reference is not None
            else unresolved.external_reference
        )
        validate_request_envelope(request, self.descriptor, AdapterKind.EXECUTOR)
        enforce_adapter_dispatch(
            request,
            self.descriptor,
            AdapterKind.EXECUTOR,
            authorization=authorization,
            current_context=current_context,
            evaluated_at=evaluated_at,
        )
        result = self._reconcile_external(request, reference)
        if result.request_id != request.request_id:
            raise AdapterContractError("reconcile result is bound to another request")
        if result.outcome is not Outcome.EXTERNAL_UNCERTAIN:
            del self._unresolved[request.idempotency_key]
        else:
            self._unresolved[request.idempotency_key] = _UnresolvedDispatch(
                request, result.external_reference or reference
            )
        return result


def known_cost(amount: Decimal, unit: str) -> CostMeasurement:
    if amount < 0 or not unit:
        raise AdapterContractError("measured cost requires a non-negative amount and unit")
    return CostMeasurement(amount=amount, unit=unit, is_unknown=False)
