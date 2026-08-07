"""Non-authorizing legacy/declarative dual-run comparison."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from video_factory.config import canonical_sha256
from video_factory.domain import HashDigest, OpaqueId

from .contracts import (
    LegacyNextStepProjection,
    ParityDifference,
    WorkflowContractError,
    WorkflowEvaluation,
    WorkflowParityReport,
)
from .evaluator import require_target_workflow_evaluation


PARITY_REPORT_VERSION = "workflow-parity-report/1.0"
PARITY_NORMALIZATION_VERSION = "legacy-next-step-normalization/1.0"
PARITY_DIMENSIONS = (
    "action",
    "blockers",
    "actor",
    "required_authority",
    "consumed_evidence",
    "prohibited_actions",
)
ALLOWED_EXPLANATIONS = {
    "EXPLAINED_FRONTIER_EXPANSION",
    "EXPLAINED_BLUEPRINT_CONSOLIDATION",
    "EXPLAINED_AUTHORITY_HARDENING",
}


def legacy_projection_from_plan(plan: object) -> LegacyNextStepProjection:
    """Project a NextStepPlan without importing authority into the legacy engine."""

    required = (
        "action_type", "next_actor_role", "approval_required", "blockers",
        "prerequisites", "prohibited_actions", "auto_execution",
    )
    if any(not hasattr(plan, name) for name in required):
        raise WorkflowContractError("workflow.parity.legacy_shape", "legacy plan shape is incomplete")
    if getattr(plan, "auto_execution") is not False:
        raise WorkflowContractError("workflow.parity.legacy_authority", "legacy projection must remain non-authorizing")
    return LegacyNextStepProjection(
        action_type=str(getattr(plan, "action_type")),
        actor_role=(
            str(getattr(plan, "next_actor_role"))
            if getattr(plan, "next_actor_role") is not None else None
        ),
        approval_required=bool(getattr(plan, "approval_required")),
        blockers=tuple(str(value) for value in getattr(plan, "blockers")),
        consumed_evidence=tuple(str(value) for value in getattr(plan, "prerequisites")),
        prohibited_actions=tuple(str(value) for value in getattr(plan, "prohibited_actions")),
        authority_effect="none",
    )


def default_parity_normalization() -> dict[str, object]:
    """Closed normalization policy; unmapped blocker text is a hard mismatch."""

    return {
        "normalization_version": PARITY_NORMALIZATION_VERSION,
        "blocker_rules": [
            {"contains": "artifact", "reason_code": "workflow.artifact_graph_valid.blocked"},
            {"contains": "brief", "reason_code": "workflow.brief_present.blocked"},
            {"contains": "storyboard", "reason_code": "workflow.storyboard_present.blocked"},
            {"contains": "review", "reason_code": "workflow.review.blocked"},
            {"contains": "approval", "reason_code": "workflow.approval.blocked"},
            {"contains": "packet", "reason_code": "workflow.packet_contract_valid.blocked"},
            {"contains": "feasibility", "reason_code": "workflow.feasibility_pass.blocked"},
            {"contains": "rapid mode", "reason_code": "workflow.mode.preview_only"},
            {"contains": "workspace", "reason_code": "workflow.workspace_trusted.blocked"},
            {"contains": "QC", "reason_code": "workflow.qc.blocked"},
            {"contains": "continuity", "reason_code": "workflow.continuity_qc.blocked"},
            {"contains": "ranking", "reason_code": "workflow.candidate_ranking_current.blocked"},
            {"contains": "edit", "reason_code": "workflow.edit_manifest_current.blocked"},
            {"contains": "rough-cut", "reason_code": "workflow.rough_cut_pass.blocked"},
            {"contains": "final-delivery", "reason_code": "workflow.final_delivery_valid.blocked"},
            {"contains": "publish metadata", "reason_code": "workflow.publish_metadata_bound.blocked"},
        ],
        "authority_by_action": {
            "approve_storyboard": "human_or_campaign",
            "approve_generation": "human_or_campaign",
            "reconcile_workspace": "two_independent_humans",
            "run_external_generation": "standing_or_one_human",
            "remediate_or_repeat_shot_qc": "standing_or_one_human",
            "select_edit_inputs": "human_or_campaign",
            "approve_publish": "human_or_campaign",
            "ready_for_human_publish": "human_or_campaign",
        },
        "default_authority": "policy",
        "unmapped_behavior": "MISMATCH",
        "authority_effect": "none",
    }


def parity_normalization_sha256(normalization: Mapping[str, object]) -> HashDigest:
    if dict(normalization) != default_parity_normalization():
        raise WorkflowContractError("workflow.parity.normalization", "normalization is not target-owned")
    return canonical_sha256(dict(normalization))


def _legacy_reason_codes(
    blockers: Sequence[str], normalization: Mapping[str, object]
) -> tuple[str, ...]:
    rules = normalization["blocker_rules"]
    assert isinstance(rules, list)
    output: list[str] = []
    for blocker in blockers:
        matches = [
            str(rule["reason_code"])
            for rule in rules
            if isinstance(rule, Mapping)
            and isinstance(rule.get("contains"), str)
            and str(rule["contains"]).casefold() in blocker.casefold()
        ]
        if not matches:
            output.append("parity.legacy_blocker_unmapped")
        else:
            output.extend(matches)
    return tuple(sorted(set(output)))


def _values(
    legacy: LegacyNextStepProjection,
    evaluation: WorkflowEvaluation,
    normalization: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    item = next(
        (
            value
            for value in evaluation.action_frontier
            if value.action_id == evaluation.recommended_action_id
        ),
        None,
    )
    if evaluation.recommended_action_id is not None and item is None:
        raise WorkflowContractError(
            "workflow.parity.recommended_action",
            "recommended action is absent from the declarative frontier",
        )
    authority_map = normalization["authority_by_action"]
    assert isinstance(authority_map, Mapping)
    legacy_authority = authority_map.get(
        legacy.action_type, normalization["default_authority"]
    )
    legacy_values = {
        "action": legacy.action_type,
        "blockers": list(_legacy_reason_codes(legacy.blockers, normalization)),
        "actor": legacy.actor_role,
        "required_authority": legacy_authority,
        "consumed_evidence": list(legacy.consumed_evidence),
        "prohibited_actions": sorted(legacy.prohibited_actions),
    }
    declarative_values = {
        "action": str(evaluation.recommended_action_id) if evaluation.recommended_action_id else None,
        "blockers": sorted(
            set(value.reason_code for value in item.blockers) if item is not None else ()
        ),
        "actor": item.actor_role if item is not None else None,
        "required_authority": item.authority_requirement.value if item is not None else None,
        "consumed_evidence": (
            sorted(str(value) for value in item.consumed_evidence_sha256s)
            if item is not None else []
        ),
        "prohibited_actions": (
            sorted(item.prohibited_actions) if item is not None else []
        ),
    }
    return legacy_values, declarative_values


def _report_identity(report: WorkflowParityReport) -> dict[str, object]:
    return {
        "artifact_version": report.artifact_version,
        "normalization_version": report.normalization_version,
        "normalization_sha256": str(report.normalization_sha256),
        "legacy_projection_sha256": str(report.legacy_projection_sha256),
        "workflow_evaluation_sha256": str(report.workflow_evaluation_sha256),
        "differences": [
            {
                "dimension": value.dimension,
                "legacy_value_sha256": str(value.legacy_value_sha256),
                "declarative_value_sha256": str(value.declarative_value_sha256),
                "explanation_code": value.explanation_code,
            }
            for value in report.differences
        ],
        "unexplained_dimensions": list(report.unexplained_dimensions),
        "parity_pass": report.parity_pass,
        "cutover_applied": report.cutover_applied,
        "authority_effect": report.authority_effect,
    }


def compare_legacy_parity(
    legacy: LegacyNextStepProjection,
    evaluation: WorkflowEvaluation,
    *,
    normalization: Mapping[str, object] | None = None,
    explained_dimensions: Mapping[str, str] | None = None,
) -> WorkflowParityReport:
    require_target_workflow_evaluation(evaluation)
    if legacy.authority_effect != "none":
        raise WorkflowContractError("workflow.parity.legacy_authority", "legacy projection cannot grant authority")
    normalized = default_parity_normalization() if normalization is None else dict(normalization)
    normalization_digest = parity_normalization_sha256(normalized)
    legacy_values, declarative_values = _values(legacy, evaluation, normalized)
    explanations = dict(explained_dimensions or {})
    unknown_explanations = set(explanations.values()) - ALLOWED_EXPLANATIONS
    if unknown_explanations or not set(explanations) <= set(PARITY_DIMENSIONS):
        raise WorkflowContractError("workflow.parity.explanation", "parity explanation is not allowlisted")
    differences: list[ParityDifference] = []
    unexplained: list[str] = []
    for dimension in PARITY_DIMENSIONS:
        legacy_digest = canonical_sha256(legacy_values[dimension])
        declarative_digest = canonical_sha256(declarative_values[dimension])
        if legacy_digest == declarative_digest:
            continue
        explanation = explanations.get(dimension)
        differences.append(
            ParityDifference(dimension, legacy_digest, declarative_digest, explanation)
        )
        if explanation is None:
            unexplained.append(dimension)
    legacy_digest = canonical_sha256(
        {
            "action_type": legacy.action_type,
            "actor_role": legacy.actor_role,
            "approval_required": legacy.approval_required,
            "blockers": list(legacy.blockers),
            "consumed_evidence": list(legacy.consumed_evidence),
            "prohibited_actions": list(legacy.prohibited_actions),
            "authority_effect": legacy.authority_effect,
        }
    )
    provisional = WorkflowParityReport(
        artifact_version=PARITY_REPORT_VERSION,
        report_id=OpaqueId("pending"),
        report_sha256=HashDigest("0" * 64),
        normalization_version=PARITY_NORMALIZATION_VERSION,
        normalization_sha256=normalization_digest,
        legacy_projection_sha256=legacy_digest,
        workflow_evaluation_sha256=evaluation.evaluation_sha256,
        differences=tuple(differences),
        unexplained_dimensions=tuple(unexplained),
        parity_pass=not unexplained,
        cutover_applied=False,
        authority_effect="none",
    )
    digest = canonical_sha256(_report_identity(provisional))
    return validate_workflow_parity_report(
        replace(
            provisional,
            report_id=OpaqueId(f"workflow-parity-{str(digest)[:20]}"),
            report_sha256=digest,
        )
    )


def workflow_parity_report_to_mapping(report: WorkflowParityReport) -> dict[str, object]:
    validate_workflow_parity_report(report)
    return {
        **_report_identity(report),
        "report_id": str(report.report_id),
        "report_sha256": str(report.report_sha256),
    }


def validate_workflow_parity_report(report: WorkflowParityReport) -> WorkflowParityReport:
    expected_normalization_sha256 = parity_normalization_sha256(
        default_parity_normalization()
    )
    expected_unexplained = tuple(
        value.dimension
        for value in report.differences
        if value.explanation_code is None
    )
    if (
        report.artifact_version != PARITY_REPORT_VERSION
        or report.normalization_version != PARITY_NORMALIZATION_VERSION
        or report.normalization_sha256 != expected_normalization_sha256
        or report.cutover_applied is not False
        or report.authority_effect != "none"
        or report.unexplained_dimensions != expected_unexplained
        or report.parity_pass != (not expected_unexplained)
    ):
        raise WorkflowContractError("workflow.parity.contract", "parity report contract is invalid")
    dimensions = tuple(value.dimension for value in report.differences)
    if dimensions != tuple(item for item in PARITY_DIMENSIONS if item in dimensions):
        raise WorkflowContractError("workflow.parity.order", "parity differences are not canonical")
    if any(
        value.explanation_code is not None and value.explanation_code not in ALLOWED_EXPLANATIONS
        for value in report.differences
    ):
        raise WorkflowContractError("workflow.parity.explanation", "parity explanation is invalid")
    digests = (
        report.normalization_sha256,
        report.legacy_projection_sha256,
        report.workflow_evaluation_sha256,
        *(value.legacy_value_sha256 for value in report.differences),
        *(value.declarative_value_sha256 for value in report.differences),
    )
    if any(
        len(str(value)) != 64
        or str(value).lower() != str(value)
        or any(character not in "0123456789abcdef" for character in str(value))
        for value in digests
    ):
        raise WorkflowContractError(
            "workflow.parity.digest",
            "parity report contains an invalid SHA-256",
        )
    digest = canonical_sha256(_report_identity(report))
    if str(digest) != str(report.report_sha256):
        raise WorkflowContractError("workflow.parity.digest", "parity report digest mismatch")
    if str(report.report_id) != f"workflow-parity-{str(digest)[:20]}":
        raise WorkflowContractError("workflow.parity.identity", "parity report id mismatch")
    return report
