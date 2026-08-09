"""Strict parse-only boundaries for W04 authority artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeAlias, cast

from video_factory.artifacts import validate_artifact_mapping
from video_factory.config import canonical_json_bytes
from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    HashDigest,
    IdempotencyKey,
    OpaqueId,
    RelativeArtifactPath,
)
from video_factory.json_boundary import (
    JsonInputError,
    parse_json_bytes,
    require_json_object,
    validate_json_mapping,
)
from video_factory.workflow import ActionRisk, AssuranceProfile, AuthorityRequirement, AutonomyProfile

from .contracts import (
    ActionRiskAssessment,
    ApprovalRequest,
    AuthorityContractError,
    AuthorityDecision,
    AuthorityDecisionStatus,
    AuthoritySource,
    AuthorityVerificationReceipt,
    LedgerRecordState,
    OutputScope,
    PolicyBundle,
    PolicyEnforcementRule,
    PrincipalSignatureVerification,
    UnverifiedStandingAuthorization,
    VerificationPurpose,
)
from .evaluator import (
    approval_request_to_mapping,
    authority_decision_to_mapping,
    authority_verification_receipt_to_mapping,
    standing_authorization_to_mapping,
    validate_approval_request,
    validate_authority_decision,
    validate_authority_verification_receipt,
    validate_standing_authorization,
)
from .policy import (
    policy_bundle_to_mapping,
    risk_assessment_to_mapping,
    validate_policy_bundle,
    validate_risk_assessment,
)


AuthorityArtifact: TypeAlias = (
    PolicyBundle
    | ActionRiskAssessment
    | UnverifiedStandingAuthorization
    | ApprovalRequest
    | AuthorityDecision
    | AuthorityVerificationReceipt
)


def _schema(document: Mapping[str, object], version: str) -> dict[str, object]:
    try:
        value = dict(validate_json_mapping(document))
    except JsonInputError as error:
        raise AuthorityContractError(error.code.value, error.detail) from error
    if value.get("artifact_version") != version:
        raise AuthorityContractError("authority.serialization.version", f"expected {version}")
    report = validate_artifact_mapping(value)
    if not report.ok:
        first = report.errors[0]
        raise AuthorityContractError(
            first.reason_code or "authority.serialization.schema", first.as_text()
        )
    return value


def _exact(value: Mapping[str, object], rendered: Mapping[str, object]) -> None:
    if dict(value) != dict(rendered):
        raise AuthorityContractError("authority.serialization.canonical", "authority artifact is not canonical")


def _reference(value: object) -> ArtifactReference:
    item = cast(Mapping[str, object], value)
    return ArtifactReference(
        path=RelativeArtifactPath(str(item["path"])),
        sha256=HashDigest(str(item["sha256"])),
        artifact_version=ArtifactVersion(str(item["artifact_version"])),
    )


def policy_bundle_from_mapping(document: Mapping[str, object]) -> PolicyBundle:
    value = _schema(document, "policy-bundle/1.0")
    result = PolicyBundle(
        artifact_version=str(value["artifact_version"]),
        bundle_id=OpaqueId(str(value["bundle_id"])),
        bundle_sha256=HashDigest(str(value["bundle_sha256"])),
        policy_version=str(value["policy_version"]),
        classifier_version=str(value["classifier_version"]),
        governance_policy_sha256=HashDigest(
            str(value["governance_policy_sha256"])
        ),
        default_decision=AuthorityDecisionStatus(str(value["default_decision"])),
        unknown_state_fail_closed=bool(value["unknown_state_fail_closed"]),
        self_approval_forbidden=bool(value["self_approval_forbidden"]),
        release_campaign_enabled=bool(value["release_campaign_enabled"]),
        maximum_r4_validity_seconds=int(
            value["maximum_r4_validity_seconds"]
        ),
        hard_escalation_triggers=tuple(
            cast(list[str], value["hard_escalation_triggers"])
        ),
        action_risk_by_action=tuple(
            (OpaqueId(str(item["action_id"])), ActionRisk(str(item["risk"])))
            for item in cast(list[dict[str, object]], value["action_risk_by_action"])
        ),
        enforcement_matrix=tuple(
            PolicyEnforcementRule(
                policy_path=str(item["policy_path"]),
                owner=str(item["owner"]),
                enforcement_phase=str(item["enforcement_phase"]),
                reason_code=str(item["reason_code"]),
                positive_test_id=str(item["positive_test_id"]),
                negative_test_id=str(item["negative_test_id"]),
            )
            for item in cast(list[dict[str, object]], value["enforcement_matrix"])
        ),
    )
    validate_policy_bundle(result)
    _exact(value, policy_bundle_to_mapping(result))
    return result


def risk_assessment_from_mapping(document: Mapping[str, object]) -> ActionRiskAssessment:
    value = _schema(document, "action-risk-assessment/1.0")
    result = ActionRiskAssessment(
        artifact_version=str(value["artifact_version"]),
        assessment_id=OpaqueId(str(value["assessment_id"])),
        assessment_sha256=HashDigest(str(value["assessment_sha256"])),
        classifier_version=str(value["classifier_version"]),
        policy_bundle_sha256=HashDigest(str(value["policy_bundle_sha256"])),
        action_request_sha256=HashDigest(str(value["action_request_sha256"])),
        effective_risk=ActionRisk(str(value["effective_risk"])),
        reason_codes=tuple(cast(list[str], value["reason_codes"])),
        supported=bool(value["supported"]),
    )
    validate_risk_assessment(result)
    _exact(value, risk_assessment_to_mapping(result))
    return result


def standing_authorization_from_mapping(
    document: Mapping[str, object],
) -> UnverifiedStandingAuthorization:
    value = _schema(document, "standing-authorization/1.0")
    result = UnverifiedStandingAuthorization(
        artifact_version=str(value["artifact_version"]),
        authorization_id=OpaqueId(str(value["authorization_id"])),
        authorization_sha256=HashDigest(str(value["authorization_sha256"])),
        gate_context_sha256=HashDigest(str(value["gate_context_sha256"])),
        capability_ids=tuple(OpaqueId(str(item)) for item in cast(list[str], value["capability_ids"])),
        workspace_ids=tuple(OpaqueId(str(item)) for item in cast(list[str], value["workspace_ids"])),
        channel_ids=tuple(OpaqueId(str(item)) for item in cast(list[str], value["channel_ids"])),
        concept_ids=tuple(OpaqueId(str(item)) for item in cast(list[str], value["concept_ids"])),
        episode_ids=tuple(OpaqueId(str(item)) for item in cast(list[str], value["episode_ids"])),
        provider_ids=tuple(OpaqueId(str(item)) for item in cast(list[str], value["provider_ids"])),
        model_ids=tuple(OpaqueId(str(item)) for item in cast(list[str], value["model_ids"])),
        destinations=tuple(cast(list[str], value["destinations"])),
        max_cost_per_run_minor=int(value["max_cost_per_run_minor"]),
        max_cost_per_day_minor=int(value["max_cost_per_day_minor"]),
        currency=str(value["currency"]),
        max_candidates=int(value["max_candidates"]),
        max_retries=int(value["max_retries"]),
        allowed_risks=tuple(ActionRisk(str(item)) for item in cast(list[str], value["allowed_risks"])),
        minimum_assurance=AssuranceProfile(str(value["minimum_assurance"])),
        maximum_autonomy=AutonomyProfile(str(value["maximum_autonomy"])),
        exact_input_artifacts=tuple(
            _reference(item)
            for item in cast(list[object], value["exact_input_artifacts"])
        ),
        allowed_outputs=tuple(
            OutputScope(
                path_prefix=str(item["path_prefix"]),
                artifact_versions=tuple(cast(list[str], item["artifact_versions"])),
            )
            for item in cast(list[dict[str, object]], value["allowed_outputs"])
        ),
        valid_from=str(value["valid_from"]),
        expires_at=str(value["expires_at"]),
        ledger_record=_reference(value["ledger_record"]),
        signature_verification_refs=tuple(_reference(item) for item in cast(list[object], value["signature_verification_refs"])),
        authority_effect=str(value["authority_effect"]),
    )
    validate_standing_authorization(result)
    _exact(value, standing_authorization_to_mapping(result))
    return result


def approval_request_from_mapping(document: Mapping[str, object]) -> ApprovalRequest:
    value = _schema(document, "approval-request/1.0")
    result = ApprovalRequest(
        artifact_version=str(value["artifact_version"]),
        approval_request_id=OpaqueId(str(value["approval_request_id"])),
        approval_request_sha256=HashDigest(str(value["approval_request_sha256"])),
        action_request_sha256=HashDigest(str(value["action_request_sha256"])),
        gate_context_sha256=HashDigest(str(value["gate_context_sha256"])),
        risk_assessment_sha256=HashDigest(str(value["risk_assessment_sha256"])),
        required_authority=AuthorityRequirement(str(value["required_authority"])),
        required_independent_humans=int(value["required_independent_humans"]),
        reason_codes=tuple(cast(list[str], value["reason_codes"])),
        material_diff_sha256=HashDigest(str(value["material_diff_sha256"])),
        safe_default=AuthorityDecisionStatus(str(value["safe_default"])),
        creates_authority=bool(value["creates_authority"]),
        authority_effect=str(value["authority_effect"]),
    )
    validate_approval_request(result)
    _exact(value, approval_request_to_mapping(result))
    return result


def authority_decision_from_mapping(document: Mapping[str, object]) -> AuthorityDecision:
    value = _schema(document, "authority-decision/1.0")
    result = AuthorityDecision(
        artifact_version=str(value["artifact_version"]),
        decision_id=OpaqueId(str(value["decision_id"])),
        decision_sha256=HashDigest(str(value["decision_sha256"])),
        action_request_sha256=HashDigest(str(value["action_request_sha256"])),
        gate_context_sha256=HashDigest(str(value["gate_context_sha256"])),
        risk_assessment_sha256=HashDigest(str(value["risk_assessment_sha256"])),
        effective_risk=ActionRisk(str(value["effective_risk"])),
        required_authority=AuthorityRequirement(str(value["required_authority"])),
        status=AuthorityDecisionStatus(str(value["status"])),
        source=AuthoritySource(str(value["source"])),
        reason_codes=tuple(cast(list[str], value["reason_codes"])),
        matched_limit_sha256=(HashDigest(str(value["matched_limit_sha256"])) if value["matched_limit_sha256"] is not None else None),
        authority_basis_sha256=(
            HashDigest(str(value["authority_basis_sha256"]))
            if value["authority_basis_sha256"] is not None
            else None
        ),
        verification_receipt_id=(OpaqueId(str(value["verification_receipt_id"])) if value["verification_receipt_id"] is not None else None),
        verification_receipt_sha256=(HashDigest(str(value["verification_receipt_sha256"])) if value["verification_receipt_sha256"] is not None else None),
        evaluated_at=str(value["evaluated_at"]),
        valid_until=str(value["valid_until"]) if value["valid_until"] is not None else None,
        predispatch_required=bool(value["predispatch_required"]),
        authority_effect=str(value["authority_effect"]),
    )
    validate_authority_decision(result)
    _exact(value, authority_decision_to_mapping(result))
    return result


def authority_verification_receipt_from_mapping(
    document: Mapping[str, object],
) -> AuthorityVerificationReceipt:
    value = _schema(document, "authority-verification-receipt/1.0")
    result = AuthorityVerificationReceipt(
        artifact_version=str(value["artifact_version"]),
        receipt_id=OpaqueId(str(value["receipt_id"])),
        receipt_sha256=HashDigest(str(value["receipt_sha256"])),
        purpose=VerificationPurpose(str(value["purpose"])),
        action_request_sha256=HashDigest(str(value["action_request_sha256"])),
        authority_decision_sha256=(HashDigest(str(value["authority_decision_sha256"])) if value["authority_decision_sha256"] is not None else None),
        gate_context_sha256=HashDigest(str(value["gate_context_sha256"])),
        risk_assessment_sha256=HashDigest(str(value["risk_assessment_sha256"])),
        workflow_evaluation_sha256=(
            HashDigest(str(value["workflow_evaluation_sha256"]))
            if value["workflow_evaluation_sha256"] is not None
            else None
        ),
        workflow_evaluation_verification_ref=(
            _reference(value["workflow_evaluation_verification_ref"])
            if value["workflow_evaluation_verification_ref"] is not None
            else None
        ),
        authority_source=AuthoritySource(str(value["authority_source"])),
        ledger_state=LedgerRecordState(str(value["ledger_state"])),
        ledger_head_sha256=HashDigest(str(value["ledger_head_sha256"])),
        ledger_entry=_reference(value["ledger_entry"]),
        requester_principal_id=OpaqueId(
            str(value["requester_principal_id"])
        ),
        requester_authentication_ref=_reference(
            value["requester_authentication_ref"]
        ),
        grant_sha256=(HashDigest(str(value["grant_sha256"])) if value["grant_sha256"] is not None else None),
        principal_verifications=tuple(
            PrincipalSignatureVerification(
                principal_id=OpaqueId(str(cast(Mapping[str, object], item)["principal_id"])),
                signature_verification_ref=_reference(
                    cast(Mapping[str, object], item)["signature_verification_ref"]
                ),
            )
            for item in cast(list[object], value["principal_verifications"])
        ),
        signature_verification_refs=tuple(_reference(item) for item in cast(list[object], value["signature_verification_refs"])),
        revocation_checked_at=str(value["revocation_checked_at"]),
        kill_switch_clear=bool(value["kill_switch_clear"]),
        reserved_cost_minor_units=int(value["reserved_cost_minor_units"]),
        currency=str(value["currency"]),
        reserved_candidates=int(value["reserved_candidates"]),
        retry_index=int(value["retry_index"]),
        idempotency_key=IdempotencyKey(str(value["idempotency_key"])),
        workspace_id=OpaqueId(str(value["workspace_id"])),
        workspace_observation_sha256=(
            HashDigest(str(value["workspace_observation_sha256"]))
            if value["workspace_observation_sha256"] is not None
            else None
        ),
        adapter_id=OpaqueId(str(value["adapter_id"])) if value["adapter_id"] is not None else None,
        service_identity=OpaqueId(str(value["service_identity"])) if value["service_identity"] is not None else None,
        evaluated_at=str(value["evaluated_at"]),
        valid_until=str(value["valid_until"]),
    )
    validate_authority_verification_receipt(result)
    _exact(value, authority_verification_receipt_to_mapping(result))
    return result


def authority_artifact_from_mapping(document: Mapping[str, object]) -> AuthorityArtifact:
    loaders = {
        "policy-bundle/1.0": policy_bundle_from_mapping,
        "action-risk-assessment/1.0": risk_assessment_from_mapping,
        "standing-authorization/1.0": standing_authorization_from_mapping,
        "approval-request/1.0": approval_request_from_mapping,
        "authority-decision/1.0": authority_decision_from_mapping,
        "authority-verification-receipt/1.0": authority_verification_receipt_from_mapping,
    }
    loader = loaders.get(document.get("artifact_version"))
    if loader is None:
        raise AuthorityContractError("authority.serialization.version", "unsupported authority artifact")
    return loader(document)


def authority_artifact_from_bytes(payload: bytes) -> AuthorityArtifact:
    return authority_artifact_from_mapping(require_json_object(parse_json_bytes(payload)))


def authority_artifact_to_mapping(artifact: AuthorityArtifact) -> dict[str, object]:
    if isinstance(artifact, PolicyBundle):
        return policy_bundle_to_mapping(artifact)
    if isinstance(artifact, ActionRiskAssessment):
        return risk_assessment_to_mapping(artifact)
    if isinstance(artifact, UnverifiedStandingAuthorization):
        return standing_authorization_to_mapping(artifact)
    if isinstance(artifact, ApprovalRequest):
        return approval_request_to_mapping(artifact)
    if isinstance(artifact, AuthorityDecision):
        return authority_decision_to_mapping(artifact)
    if isinstance(artifact, AuthorityVerificationReceipt):
        return authority_verification_receipt_to_mapping(artifact)
    raise AuthorityContractError("authority.serialization.type", "unsupported authority artifact")


def authority_artifact_to_bytes(artifact: AuthorityArtifact) -> bytes:
    return canonical_json_bytes(authority_artifact_to_mapping(artifact))
