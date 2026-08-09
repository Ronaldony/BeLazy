"""Exact W04 initial-authority binding shared by W05 decisions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from video_factory.approvals import GateContext, gate_context_sha256
from video_factory.authority import (
    ActionAuthorityRequest,
    ActionRiskAssessment,
    AuthorityDecision,
    AuthorityDecisionStatus,
    AuthorityVerificationReceipt,
    LedgerRecordState,
    TrustedAuthorizationLedger,
    UnverifiedStandingAuthorization,
    VerificationPurpose,
    evaluate_authority,
    target_policy_bundle,
    validate_action_authority_request,
    validate_authority_decision,
    validate_authority_verification_receipt,
    validate_risk_assessment,
)
from video_factory.domain import ArtifactReference
from video_factory.json_boundary import parse_rfc3339_datetime

from .contracts import QualityContractError
from .validation import require_gate_context, require_reference_consistency


@dataclass(frozen=True, slots=True)
class InitialAuthorityEvidence:
    request: ActionAuthorityRequest
    risk: ActionRiskAssessment
    decision: AuthorityDecision
    receipt: AuthorityVerificationReceipt

    def __post_init__(self) -> None:
        try:
            validate_action_authority_request(self.request)
            validate_risk_assessment(self.risk)
            validate_authority_decision(self.decision)
            validate_authority_verification_receipt(self.receipt)
        except (AttributeError, TypeError, ValueError) as error:
            raise QualityContractError(
                "quality.authority.invalid",
                "authority evidence is structurally invalid",
            ) from error
        request = self.request
        risk = self.risk
        decision = self.decision
        receipt = self.receipt
        request_context_sha256 = gate_context_sha256(request.gate_context)
        if (
            not risk.supported
            or risk.action_request_sha256 != request.request_sha256
            or decision.action_request_sha256 != request.request_sha256
            or decision.gate_context_sha256 != request_context_sha256
            or decision.risk_assessment_sha256 != risk.assessment_sha256
            or decision.effective_risk is not risk.effective_risk
            or decision.status is not AuthorityDecisionStatus.AUTHORIZED
            or decision.authority_effect != "execution_authority"
            or decision.verification_receipt_id != receipt.receipt_id
            or decision.verification_receipt_sha256 != receipt.receipt_sha256
            or receipt.purpose is not VerificationPurpose.INITIAL_DECISION
            or receipt.authority_decision_sha256 is not None
            or receipt.action_request_sha256 != request.request_sha256
            or receipt.gate_context_sha256 != request_context_sha256
            or receipt.risk_assessment_sha256 != risk.assessment_sha256
            or receipt.authority_source is not decision.source
            or receipt.ledger_state is not LedgerRecordState.ACTIVE
            or not receipt.kill_switch_clear
            or receipt.requester_principal_id != request.requester_principal_id
            or receipt.idempotency_key != request.idempotency_key
        ):
            raise QualityContractError(
                "quality.authority.binding",
                "authority request, risk, decision, and receipt are not coherent",
            )
        issued = parse_rfc3339_datetime(receipt.evaluated_at)
        valid_until = parse_rfc3339_datetime(receipt.valid_until)
        decision_issued = parse_rfc3339_datetime(decision.evaluated_at)
        if (
            issued != decision_issued
            or issued >= valid_until
            or decision.valid_until is None
            or parse_rfc3339_datetime(decision.valid_until) != valid_until
        ):
            raise QualityContractError(
                "quality.authority.window",
                "authority decision and receipt validity windows are incoherent",
            )


def validate_initial_authority_evidence(
    evidence: InitialAuthorityEvidence,
    *,
    current_context: GateContext,
    evaluated_at: datetime,
    expected_action_id: str,
    expected_capability_id: str,
    expected_plan_sha256: str,
    expected_input_artifacts: tuple[ArtifactReference, ...],
    expected_destination: str | None,
    expected_side_effect: bool,
    ledger: TrustedAuthorizationLedger,
    authority_references: Sequence[ArtifactReference] = (),
    presented_grant: UnverifiedStandingAuthorization | None = None,
) -> InitialAuthorityEvidence:
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise QualityContractError("quality.authority.time", "authority evaluation time must be timezone-aware")
    require_gate_context(current_context)
    try:
        validate_action_authority_request(evidence.request)
        validate_risk_assessment(evidence.risk)
        validate_authority_decision(evidence.decision)
        validate_authority_verification_receipt(evidence.receipt)
    except (AttributeError, TypeError, ValueError) as error:
        raise QualityContractError("quality.authority.invalid", "authority evidence is structurally invalid") from error
    request = evidence.request
    risk = evidence.risk
    decision = evidence.decision
    receipt = evidence.receipt
    try:
        fresh_risk, fresh_decision = evaluate_authority(
            request,
            target_policy_bundle(),
            ledger=ledger,
            presented_grant=presented_grant,
            authority_references=authority_references,
            evaluated_at=evaluated_at,
        )
    except Exception as error:
        raise QualityContractError(
            "quality.authority.ledger_unavailable",
            "trusted current authority verification failed",
        ) from error
    if fresh_risk != risk or fresh_decision != decision:
        raise QualityContractError(
            "quality.authority.not_current",
            "persisted authority does not match trusted current recomputation",
        )
    expected_refs = require_reference_consistency(expected_input_artifacts, allow_exact_reuse=False)
    actual_refs = require_reference_consistency(request.scope.input_artifacts, allow_exact_reuse=False)
    if (
        str(request.action_id) != expected_action_id
        or str(request.capability_id) != expected_capability_id
        or str(request.executable_plan_sha256) != expected_plan_sha256
        or request.gate_context != current_context
        or request.scope.input_artifacts != expected_refs
        or actual_refs != expected_refs
        or request.scope.destination != expected_destination
        or request.side_effect is not expected_side_effect
    ):
        raise QualityContractError("quality.authority.scope", "authority request is bound to another action, context, input, or destination")
    if (
        not risk.supported
        or risk.action_request_sha256 != request.request_sha256
        or decision.action_request_sha256 != request.request_sha256
        or decision.gate_context_sha256 != gate_context_sha256(current_context)
        or decision.risk_assessment_sha256 != risk.assessment_sha256
        or decision.effective_risk is not risk.effective_risk
        or decision.status is not AuthorityDecisionStatus.AUTHORIZED
        or decision.authority_effect != "execution_authority"
        or decision.verification_receipt_id != receipt.receipt_id
        or decision.verification_receipt_sha256 != receipt.receipt_sha256
    ):
        raise QualityContractError("quality.authority.decision", "authority decision does not authorize the exact current request")
    if (
        receipt.purpose is not VerificationPurpose.INITIAL_DECISION
        or receipt.authority_decision_sha256 is not None
        or receipt.action_request_sha256 != request.request_sha256
        or receipt.gate_context_sha256 != gate_context_sha256(current_context)
        or receipt.risk_assessment_sha256 != risk.assessment_sha256
        or receipt.authority_source is not decision.source
        or receipt.ledger_state is not LedgerRecordState.ACTIVE
        or not receipt.kill_switch_clear
        or receipt.requester_principal_id != request.requester_principal_id
        or receipt.idempotency_key != request.idempotency_key
    ):
        raise QualityContractError("quality.authority.receipt", "initial ledger receipt is not current for the exact request")
    issued = parse_rfc3339_datetime(receipt.evaluated_at)
    valid_until = parse_rfc3339_datetime(receipt.valid_until)
    decision_issued = parse_rfc3339_datetime(decision.evaluated_at)
    if issued != decision_issued or evaluated_at < issued or evaluated_at >= valid_until:
        raise QualityContractError("quality.authority.expired", "authority evidence is future-dated or expired")
    if decision.valid_until is None or parse_rfc3339_datetime(decision.valid_until) != valid_until:
        raise QualityContractError("quality.authority.window", "decision and ledger validity windows differ")
    return evidence


__all__ = ["InitialAuthorityEvidence", "validate_initial_authority_evidence"]
