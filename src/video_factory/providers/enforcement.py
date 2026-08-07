"""Mode checks at orchestration and immediate external-dispatch boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from video_factory.approvals import (
    ApprovalEvidence,
    ApprovalRequirement,
    GateContext,
    gate_context_from_mapping,
    gate_context_sha256,
    gate_context_to_mapping,
    validate_evidence_binding,
)
from video_factory.config.canonical import canonical_sha256
from video_factory.domain import CapabilityId
from video_factory.engine.contracts import ExecutionMode
from video_factory.engine.mode import mode_is_within_limit
from video_factory.json_boundary import parse_rfc3339_datetime
from video_factory.mutation import (
    WorkspaceObservation,
    workspace_observation_sha256,
    workspace_trust_blockers,
)

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
    request_envelope_sha256: str
    human_evidence_id: str | None
    gate_context_sha256: str | None = None
    evaluated_at: str | None = None
    valid_until: str | None = None
    workspace_revision_id: str | None = None
    workspace_observation_sha256: str | None = None


def _reject(message: str) -> None:
    raise ModeEnforcementError(message)


def request_envelope_sha256(request: RequestEnvelope) -> str:
    """Canonical digest of every authority-relevant request field."""

    return str(
        canonical_sha256(
            {
                "request_id": str(request.request_id),
                "capability_id": str(request.capability_id),
                "effective_execution_mode": request.effective_execution_mode.value,
                "effective_config_sha256": str(request.effective_config_sha256),
                "input_artifacts": [
                    {
                        "path": str(item.path),
                        "sha256": str(item.sha256),
                        "artifact_version": str(item.artifact_version),
                    }
                    for item in request.input_artifacts
                ],
                "allowed_outputs": [
                    {
                        "path_prefix": str(item.path_prefix),
                        "artifact_versions": sorted(
                            str(version) for version in item.artifact_versions
                        ),
                    }
                    for item in request.allowed_outputs
                ],
                "idempotency_key": str(request.idempotency_key),
                "creator_role": str(request.creator_role),
                "reviewer_role": str(request.reviewer_role),
            }
        )
    )


def _validate_human_evidence(
    request: RequestEnvelope,
    evidence: ApprovalEvidence | None,
    *,
    current_context: GateContext | None,
    evaluated_at: datetime | None,
) -> tuple[str, str]:
    if evidence is None:
        _reject("required human evidence is missing")
    requirement = ApprovalRequirement(
        requirement_id=evidence.requirement.requirement_id,
        capability_id=request.capability_id,
        bound_artifacts=request.input_artifacts,
        effective_config_sha256=request.effective_config_sha256,
        gate_context=current_context,
    )
    result = validate_evidence_binding(
        requirement,
        evidence,
        current_context=current_context,
        evaluated_at=evaluated_at,
    )
    if not result.ok:
        _reject(result.message)
    assert evidence.expires_at is not None
    return str(evidence.evidence_id), evidence.expires_at


class OrchestrationGuard:
    """ADR-004 point 2: authorize before reservation or adapter lookup."""

    def authorize(
        self,
        request: RequestEnvelope,
        policy: OrchestrationPolicy,
        evidence: ApprovalEvidence | None = None,
        *,
        current_context: GateContext | None = None,
        evaluated_at: datetime | None = None,
        workspace_observation: WorkspaceObservation | None = None,
        expected_workspace_revision_id: str | None = None,
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
        if (
            policy.human_evidence_required
            and request.effective_execution_mode is not ExecutionMode.PREVIEW_ONLY
            and evidence is None
        ):
            _reject("required human evidence is missing")

        normalized_context: GateContext | None = None
        normalized_workspace_sha256: str | None = None
        if request.effective_execution_mode is not ExecutionMode.PREVIEW_ONLY:
            if current_context is None:
                _reject("current gate context is required for production authorization")
            try:
                normalized_context = gate_context_from_mapping(
                    gate_context_to_mapping(current_context)
                )
            except ValueError:
                _reject("current gate context is invalid")
            if (
                normalized_context.effective_config_sha256
                != request.effective_config_sha256
            ):
                _reject("request effective config does not match current gate context")
            if (
                evaluated_at is None
                or evaluated_at.tzinfo is None
                or evaluated_at.utcoffset() is None
            ):
                _reject("timezone-aware evaluation time is required for authorization")
            workspace_blockers = workspace_trust_blockers(
                workspace_observation,
                expected_revision_id=expected_workspace_revision_id,
                expected_manifest_sha256=normalized_context.current_manifest_sha256,
            )
            if workspace_blockers:
                _reject(
                    "current workspace observation rejected: "
                    + ", ".join(workspace_blockers)
                )
            assert workspace_observation is not None
            normalized_workspace_sha256 = str(
                workspace_observation_sha256(workspace_observation)
            )

        evidence_id = None
        valid_until = None
        if (
            policy.human_evidence_required
            and request.effective_execution_mode is not ExecutionMode.PREVIEW_ONLY
        ):
            evidence_id, valid_until = _validate_human_evidence(
                request,
                evidence,
                current_context=normalized_context,
                evaluated_at=evaluated_at,
            )

        return OrchestrationAuthorization(
            request_id=str(request.request_id),
            capability_id=request.capability_id,
            execution_mode=request.effective_execution_mode,
            request_envelope_sha256=request_envelope_sha256(request),
            human_evidence_id=evidence_id,
            gate_context_sha256=(
                str(gate_context_sha256(normalized_context))
                if normalized_context is not None
                else None
            ),
            evaluated_at=(
                evaluated_at.isoformat() if evaluated_at is not None else None
            ),
            valid_until=valid_until,
            workspace_revision_id=(
                str(workspace_observation.revision_id)
                if workspace_observation is not None
                and request.effective_execution_mode is not ExecutionMode.PREVIEW_ONLY
                else None
            ),
            workspace_observation_sha256=normalized_workspace_sha256,
        )


def enforce_adapter_dispatch(
    request: RequestEnvelope,
    descriptor: CapabilityDescriptor,
    expected_kind: AdapterKind,
    *,
    authorization: OrchestrationAuthorization | None = None,
    current_context: GateContext | None = None,
    evaluated_at: datetime | None = None,
    workspace_observation: WorkspaceObservation | None = None,
    expected_workspace_revision_id: str | None = None,
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
    if authorization is None:
        _reject("executor dispatch requires orchestration authorization")
    if current_context is None:
        _reject("executor dispatch requires current gate context")
    if (
        evaluated_at is None
        or evaluated_at.tzinfo is None
        or evaluated_at.utcoffset() is None
    ):
        _reject("executor dispatch requires timezone-aware evaluation time")
    try:
        normalized_context = gate_context_from_mapping(
            gate_context_to_mapping(current_context)
        )
    except ValueError:
        _reject("executor dispatch current gate context is invalid")
    if normalized_context.effective_config_sha256 != request.effective_config_sha256:
        _reject("executor request config does not match current gate context")
    if authorization.request_id != str(request.request_id):
        _reject("executor authorization is bound to another request")
    if authorization.capability_id != request.capability_id:
        _reject("executor authorization is bound to another capability")
    if authorization.execution_mode is not request.effective_execution_mode:
        _reject("executor authorization is bound to another execution mode")
    if authorization.request_envelope_sha256 != request_envelope_sha256(request):
        _reject("executor authorization is bound to another exact request envelope")
    if authorization.gate_context_sha256 != str(
        gate_context_sha256(normalized_context)
    ):
        _reject("executor authorization is bound to another gate context")
    workspace_blockers = workspace_trust_blockers(
        workspace_observation,
        expected_revision_id=expected_workspace_revision_id,
        expected_manifest_sha256=normalized_context.current_manifest_sha256,
    )
    if workspace_blockers:
        _reject(
            "executor current workspace observation rejected: "
            + ", ".join(workspace_blockers)
        )
    assert workspace_observation is not None
    if authorization.workspace_revision_id != str(workspace_observation.revision_id):
        _reject("executor authorization is bound to another workspace revision")
    if authorization.workspace_observation_sha256 != str(
        workspace_observation_sha256(workspace_observation)
    ):
        _reject("executor authorization is bound to another workspace observation")
    if authorization.evaluated_at is None:
        _reject("executor authorization evaluation time is missing")
    try:
        authorized_at = parse_rfc3339_datetime(authorization.evaluated_at)
    except ValueError:
        _reject("executor authorization evaluation time is invalid")
    if authorized_at > evaluated_at:
        _reject("executor authorization is not yet valid")
    if authorization.human_evidence_id is not None:
        if authorization.valid_until is None:
            _reject("executor authorization evidence expiry is missing")
        try:
            valid_until = parse_rfc3339_datetime(authorization.valid_until)
        except ValueError:
            _reject("executor authorization evidence expiry is invalid")
        if evaluated_at >= valid_until:
            _reject("executor authorization evidence has expired")
