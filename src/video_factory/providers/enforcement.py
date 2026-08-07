"""Mode checks at orchestration and immediate external-dispatch boundaries."""

from __future__ import annotations

from dataclasses import dataclass

from video_factory.approvals import ApprovalEvidence, ApprovalState
from video_factory.domain import CapabilityId
from video_factory.engine.contracts import ExecutionMode
from video_factory.engine.mode import mode_is_within_limit

from .contracts import AdapterKind, CapabilityDescriptor, RequestEnvelope


class ModeEnforcementError(ValueError):
    """Raised before reservation, selection, or an external side effect."""


@dataclass(frozen=True, slots=True)
class OrchestrationPolicy:
    """Hash-bound policy facts available before adapter selection."""

    capability_id: CapabilityId
    adapter_kind: AdapterKind
    effective_execution_mode: ExecutionMode
    human_evidence_required: bool


@dataclass(frozen=True, slots=True)
class OrchestrationAuthorization:
    request_id: str
    capability_id: CapabilityId
    execution_mode: ExecutionMode
    human_evidence_id: str | None


def _reject(message: str) -> None:
    raise ModeEnforcementError(message)


def _validate_human_evidence(
    request: RequestEnvelope,
    evidence: ApprovalEvidence | None,
) -> str:
    if evidence is None:
        _reject("required human evidence is missing")
    if evidence.state is not ApprovalState.GRANTED:
        _reject("human evidence is not granted")
    requirement = evidence.requirement
    if requirement.capability_id != request.capability_id:
        _reject("human evidence capability does not match the request")
    if requirement.effective_config_sha256 != request.effective_config_sha256:
        _reject("human evidence is bound to another effective config")
    if requirement.bound_artifacts != request.input_artifacts:
        _reject("human evidence is bound to different input artifacts")
    return str(evidence.evidence_id)


class OrchestrationGuard:
    """ADR-004 point 2: authorize before reservation or adapter lookup."""

    def authorize(
        self,
        request: RequestEnvelope,
        policy: OrchestrationPolicy,
        evidence: ApprovalEvidence | None = None,
    ) -> OrchestrationAuthorization:
        if request.capability_id != policy.capability_id:
            _reject("request capability does not match orchestration policy")
        if request.effective_execution_mode is not policy.effective_execution_mode:
            _reject("request mode does not match the effective-config decision")
        if not mode_is_within_limit(
            request.effective_execution_mode,
            policy.effective_execution_mode,
        ):
            _reject("request mode exceeds the orchestration maximum")
        if (
            policy.adapter_kind is AdapterKind.PROVIDER
            and request.effective_execution_mode is ExecutionMode.AUTOMATED
        ):
            _reject("provider automation is forbidden by the current media-generation contract")

        evidence_id = None
        if (
            policy.human_evidence_required
            and request.effective_execution_mode is not ExecutionMode.PREVIEW_ONLY
        ):
            evidence_id = _validate_human_evidence(request, evidence)

        return OrchestrationAuthorization(
            request_id=str(request.request_id),
            capability_id=request.capability_id,
            execution_mode=request.effective_execution_mode,
            human_evidence_id=evidence_id,
        )


def enforce_adapter_dispatch(
    request: RequestEnvelope,
    descriptor: CapabilityDescriptor,
    expected_kind: AdapterKind,
) -> None:
    """ADR-004 point 4: recheck immediately before an external process."""

    if descriptor.adapter_kind is not expected_kind:
        _reject("adapter kind does not match the dispatch contract")
    if request.capability_id not in descriptor.capabilities:
        _reject("adapter does not declare the requested capability")
    if request.effective_execution_mode not in descriptor.supported_execution_modes:
        _reject("adapter does not support the effective execution mode")
    if expected_kind is AdapterKind.PROVIDER:
        _reject("provider dispatch is disabled; use a human handoff")
    if request.effective_execution_mode is not ExecutionMode.AUTOMATED:
        _reject("external executor dispatch requires automated mode")
