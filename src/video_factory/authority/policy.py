"""Target-owned authority policy bundle and deterministic risk classifier."""

from __future__ import annotations

from video_factory.config import canonical_sha256
from video_factory.domain import HashDigest, OpaqueId
from video_factory.workflow import ActionRisk, ExecutableProductionPlan, default_workflow_definition

from .contracts import (
    ActionAuthorityRequest,
    ActionRiskAssessment,
    AuthorityContractError,
    AuthorityDecisionStatus,
    PolicyBundle,
    PolicyEnforcementRule,
)


POLICY_ARTIFACT_VERSION = "policy-bundle/1.0"
RISK_ASSESSMENT_VERSION = "action-risk-assessment/1.0"
POLICY_VERSION = "authority-policy/2.1"
CLASSIFIER_VERSION = "authority-risk-classifier/1.0"


_ENFORCEMENT_ROWS = (
    ("default_decision", "authority.policy", "initial", "authority.policy.default_deny"),
    ("unknown_state_behavior", "authority.policy", "initial", "authority.policy.unknown"),
    ("ai_human_approval_creation_forbidden", "authority.requests", "serialization", "authority.ai_cannot_grant"),
    ("executor_revalidation_required", "authority.guard", "predispatch", "authority.predispatch.required"),
    ("material_change_invalidates_authority", "authority.guard", "initial", "authority.context.mismatch"),
    ("scope.capability", "authority.scope", "initial", "authority.scope.capability"),
    ("scope.channel_concept_episode", "authority.scope", "initial", "authority.scope.subject"),
    ("scope.provider_model", "authority.scope", "initial", "authority.scope.provider"),
    ("scope.destination", "authority.scope", "initial", "authority.scope.destination"),
    ("scope.cost_per_run", "authority.scope", "initial", "authority.scope.cost_run"),
    ("scope.cost_per_day", "authority.ledger", "predispatch", "authority.scope.cost_day"),
    ("scope.candidates", "authority.scope", "initial", "authority.scope.candidates"),
    ("scope.retries", "authority.scope", "initial", "authority.scope.retries"),
    ("validity", "authority.ledger", "initial", "authority.grant.expired"),
    ("revocation", "authority.ledger", "predispatch", "authority.grant.revoked"),
    ("kill_switch", "authority.ledger", "predispatch", "authority.kill_switch.engaged"),
    ("R4.independent_approvers", "authority.ledger", "initial", "authority.r4.principals"),
    ("R4.standing_grant_forbidden", "authority.policy", "initial", "authority.r4.standing_forbidden"),
)


def _enforcement_matrix() -> tuple[PolicyEnforcementRule, ...]:
    return tuple(
        PolicyEnforcementRule(
            policy_path=path,
            owner=owner,
            enforcement_phase=phase,
            reason_code=reason,
            positive_test_id=f"test_policy_{index:02d}_positive",
            negative_test_id=f"test_policy_{index:02d}_negative",
        )
        for index, (path, owner, phase, reason) in enumerate(_ENFORCEMENT_ROWS, 1)
    )


def _bundle_identity(bundle: PolicyBundle) -> dict[str, object]:
    return {
        "artifact_version": bundle.artifact_version,
        "policy_version": bundle.policy_version,
        "classifier_version": bundle.classifier_version,
        "default_decision": bundle.default_decision.value,
        "unknown_state_fail_closed": bundle.unknown_state_fail_closed,
        "action_risk_by_action": [
            {"action_id": str(action_id), "risk": risk.value}
            for action_id, risk in bundle.action_risk_by_action
        ],
        "enforcement_matrix": [
            {
                "policy_path": row.policy_path,
                "owner": row.owner,
                "enforcement_phase": row.enforcement_phase,
                "reason_code": row.reason_code,
                "positive_test_id": row.positive_test_id,
                "negative_test_id": row.negative_test_id,
            }
            for row in bundle.enforcement_matrix
        ],
    }


def policy_bundle_to_mapping(bundle: PolicyBundle) -> dict[str, object]:
    validate_policy_bundle(bundle)
    return {
        **_bundle_identity(bundle),
        "bundle_id": str(bundle.bundle_id),
        "bundle_sha256": str(bundle.bundle_sha256),
    }


def target_policy_bundle() -> PolicyBundle:
    definition = default_workflow_definition()
    risk_map = tuple((item.action_id, item.risk) for item in definition.actions) + (
        (OpaqueId("managed_mutation"), ActionRisk.R4),
    )
    provisional = PolicyBundle(
        artifact_version=POLICY_ARTIFACT_VERSION,
        bundle_id=OpaqueId("pending"),
        bundle_sha256=HashDigest("0" * 64),
        policy_version=POLICY_VERSION,
        classifier_version=CLASSIFIER_VERSION,
        default_decision=AuthorityDecisionStatus.DENIED,
        unknown_state_fail_closed=True,
        action_risk_by_action=risk_map,
        enforcement_matrix=_enforcement_matrix(),
    )
    digest = canonical_sha256(_bundle_identity(provisional))
    return validate_policy_bundle(
        PolicyBundle(
            artifact_version=provisional.artifact_version,
            bundle_id=OpaqueId(f"policy-bundle-{str(digest)[:20]}"),
            bundle_sha256=digest,
            policy_version=provisional.policy_version,
            classifier_version=provisional.classifier_version,
            default_decision=provisional.default_decision,
            unknown_state_fail_closed=provisional.unknown_state_fail_closed,
            action_risk_by_action=provisional.action_risk_by_action,
            enforcement_matrix=provisional.enforcement_matrix,
        )
    )


def validate_policy_bundle(bundle: PolicyBundle) -> PolicyBundle:
    if (
        bundle.artifact_version != POLICY_ARTIFACT_VERSION
        or bundle.policy_version != POLICY_VERSION
        or bundle.classifier_version != CLASSIFIER_VERSION
        or bundle.default_decision is not AuthorityDecisionStatus.DENIED
        or bundle.unknown_state_fail_closed is not True
    ):
        raise AuthorityContractError("authority.policy.contract", "policy bundle is not fail closed")
    action_ids = tuple(str(value) for value, _ in bundle.action_risk_by_action)
    if action_ids != tuple(dict.fromkeys(action_ids)):
        raise AuthorityContractError("authority.policy.action_duplicate", "policy action ids are duplicated")
    matrix_paths = tuple(row.policy_path for row in bundle.enforcement_matrix)
    if set(matrix_paths) != {row[0] for row in _ENFORCEMENT_ROWS} or len(matrix_paths) != len(set(matrix_paths)):
        raise AuthorityContractError("authority.policy.matrix", "policy enforcement matrix is incomplete")
    for row in bundle.enforcement_matrix:
        if not all(
            value and value.strip() == value
            for value in (
                row.policy_path,
                row.owner,
                row.enforcement_phase,
                row.reason_code,
                row.positive_test_id,
                row.negative_test_id,
            )
        ):
            raise AuthorityContractError("authority.policy.matrix", "policy enforcement row is invalid")
    digest = canonical_sha256(_bundle_identity(bundle))
    if str(digest) != str(bundle.bundle_sha256):
        raise AuthorityContractError("authority.policy.digest", "policy bundle digest mismatch")
    if str(bundle.bundle_id) != f"policy-bundle-{str(digest)[:20]}":
        raise AuthorityContractError("authority.policy.identity", "policy bundle id mismatch")
    return bundle


def require_target_policy_bundle(bundle: PolicyBundle) -> PolicyBundle:
    validate_policy_bundle(bundle)
    if bundle != target_policy_bundle():
        raise AuthorityContractError("authority.policy.not_target_owned", "authority requires the exact target policy bundle")
    return bundle


def _risk_identity(assessment: ActionRiskAssessment) -> dict[str, object]:
    return {
        "artifact_version": assessment.artifact_version,
        "classifier_version": assessment.classifier_version,
        "policy_bundle_sha256": str(assessment.policy_bundle_sha256),
        "action_request_sha256": str(assessment.action_request_sha256),
        "effective_risk": assessment.effective_risk.value,
        "reason_codes": list(assessment.reason_codes),
        "supported": assessment.supported,
    }


def risk_assessment_to_mapping(assessment: ActionRiskAssessment) -> dict[str, object]:
    validate_risk_assessment(assessment)
    return {
        **_risk_identity(assessment),
        "assessment_id": str(assessment.assessment_id),
        "assessment_sha256": str(assessment.assessment_sha256),
    }


def classify_action_risk(
    request: ActionAuthorityRequest,
    policy: PolicyBundle,
) -> ActionRiskAssessment:
    require_target_policy_bundle(policy)
    risk_by_action = {str(key): value for key, value in policy.action_risk_by_action}
    action_id = str(request.action_id)
    supported = action_id in risk_by_action
    effective = risk_by_action.get(action_id, ActionRisk.R4)
    reasons = (
        (f"authority.risk.target_action.{effective.value.lower()}",)
        if supported
        else ("authority.risk.unknown_action", "authority.risk.escalated_r4")
    )
    provisional = ActionRiskAssessment(
        artifact_version=RISK_ASSESSMENT_VERSION,
        assessment_id=OpaqueId("pending"),
        assessment_sha256=HashDigest("0" * 64),
        classifier_version=policy.classifier_version,
        policy_bundle_sha256=policy.bundle_sha256,
        action_request_sha256=request.request_sha256,
        effective_risk=effective,
        reason_codes=reasons,
        supported=supported,
    )
    digest = canonical_sha256(_risk_identity(provisional))
    return validate_risk_assessment(
        ActionRiskAssessment(
            artifact_version=provisional.artifact_version,
            assessment_id=OpaqueId(f"risk-assessment-{str(digest)[:20]}"),
            assessment_sha256=digest,
            classifier_version=provisional.classifier_version,
            policy_bundle_sha256=provisional.policy_bundle_sha256,
            action_request_sha256=provisional.action_request_sha256,
            effective_risk=provisional.effective_risk,
            reason_codes=provisional.reason_codes,
            supported=provisional.supported,
        )
    )


def validate_risk_assessment(assessment: ActionRiskAssessment) -> ActionRiskAssessment:
    if assessment.artifact_version != RISK_ASSESSMENT_VERSION or assessment.classifier_version != CLASSIFIER_VERSION:
        raise AuthorityContractError("authority.risk.version", "risk assessment version is invalid")
    if not assessment.reason_codes or len(assessment.reason_codes) != len(set(assessment.reason_codes)):
        raise AuthorityContractError("authority.risk.reasons", "risk reasons must be non-empty and unique")
    digest = canonical_sha256(_risk_identity(assessment))
    if str(digest) != str(assessment.assessment_sha256):
        raise AuthorityContractError("authority.risk.digest", "risk assessment digest mismatch")
    if str(assessment.assessment_id) != f"risk-assessment-{str(digest)[:20]}":
        raise AuthorityContractError("authority.risk.identity", "risk assessment id mismatch")
    return assessment
