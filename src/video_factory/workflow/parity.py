"""Non-authorizing legacy/declarative dual-run comparison."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from video_factory.config import canonical_sha256
from video_factory.domain import HashDigest, OpaqueId

from .contracts import (
    AuthorityRequirement,
    LegacyNextStepProjection,
    ParityDifference,
    WorkflowContractError,
    WorkflowEvaluation,
    WorkflowParityReport,
)
from .evaluator import require_target_workflow_evaluation
from .definition import default_workflow_definition


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
    "EXPLAINED_PROCESS_CONSOLIDATION",
}
EXPLANATION_DIMENSIONS = {
    "EXPLAINED_FRONTIER_EXPANSION": frozenset({"blockers"}),
    "EXPLAINED_BLUEPRINT_CONSOLIDATION": frozenset({"consumed_evidence"}),
    "EXPLAINED_AUTHORITY_HARDENING": frozenset({"required_authority"}),
    "EXPLAINED_PROCESS_CONSOLIDATION": frozenset(
        {
            "action",
            "blockers",
            "actor",
            "required_authority",
            "consumed_evidence",
        }
    ),
}


_PARITY_ACTION_ROWS = {
    "resolve_artifact_graph": (
        ("workflow.artifact_graph_valid.blocked", "workflow.brief_present.blocked"),
        ("workflow.artifact_graph_valid.blocked",),
        (),
    ),
    "create_brief": (
        ("workflow.brief_present.blocked",),
        ("workflow.brief_present.blocked",),
        (),
    ),
    "create_storyboard": (
        ("workflow.storyboard_present.blocked",),
        ("workflow.storyboard_present.blocked",),
        ("brief",),
    ),
    "review_or_revise_storyboard": (
        ("workflow.review.blocked", "workflow.storyboard_present.blocked"),
        ("workflow.review.blocked",),
        ("storyboard",),
    ),
    "approve_storyboard": (
        (
            "workflow.approval.blocked",
            "workflow.artifact_graph_valid.blocked",
            "workflow.storyboard_present.blocked",
        ),
        ("workflow.approval.blocked",),
        ("storyboard", "passing storyboard reviews"),
    ),
    "create_generation_packet": (
        ("workflow.packet_contract_valid.blocked",),
        ("workflow.packet_present.blocked",),
        ("storyboard approval",),
    ),
    "rebuild_generation_packet": (
        (
            "workflow.packet_contract_valid.blocked",
            "workflow.storyboard_present.blocked",
        ),
        ("workflow.packet_contract_valid.blocked",),
        (),
    ),
    "review_or_revise_generation_packet": (
        ("workflow.packet_contract_valid.blocked", "workflow.review.blocked"),
        ("workflow.review.blocked",),
        ("generation-packet",),
    ),
    "review_generation_feasibility": (
        (
            "workflow.artifact_graph_valid.blocked",
            "workflow.feasibility_pass.blocked",
            "workflow.review.blocked",
        ),
        ("workflow.feasibility_pass.blocked",),
        ("generation-packet", "storyboard"),
    ),
    "approve_generation": (
        (
            "workflow.approval.blocked",
            "workflow.artifact_graph_valid.blocked",
            "workflow.packet_contract_valid.blocked",
        ),
        ("workflow.approval.blocked",),
        ("packet review pass", "feasibility pass"),
    ),
    "preview_complete": (
        ("workflow.mode.preview_only",),
        ("workflow.mode.preview_only",),
        (),
    ),
    "reconcile_workspace": (
        ("workflow.workspace_trusted.blocked",),
        ("workflow.workspace_trusted.blocked",),
        ("trusted workspace observation",),
    ),
    "run_external_generation": (
        ("workflow.qc.blocked",),
        ("workflow.shot_qc.missing",),
        ("generation readiness",),
    ),
    "remediate_or_repeat_shot_qc": (
        ("workflow.qc.blocked",),
        ("workflow.shot_qc.failed",),
        ("generation readiness",),
    ),
    "run_continuity_qc": (
        (
            "workflow.artifact_graph_valid.blocked",
            "workflow.continuity_qc.blocked",
            "workflow.packet_contract_valid.blocked",
            "workflow.qc.blocked",
        ),
        ("workflow.continuity_qc.missing",),
        (),
    ),
    "remediate_continuity_qc": (
        ("workflow.continuity_qc.blocked", "workflow.qc.blocked"),
        ("workflow.continuity_qc.failed",),
        (),
    ),
    "rank_generation_candidates": (
        ("workflow.candidate_ranking_current.blocked",),
        ("workflow.candidate_ranking_current.blocked",),
        (),
    ),
    "select_edit_inputs": (
        ("workflow.edit_manifest_current.blocked",),
        ("workflow.edit_manifest_current.blocked",),
        (),
    ),
    "assemble_or_repair_rough_cut": (
        ("workflow.rough_cut_pass.blocked",),
        ("workflow.rough_cut_pass.blocked",),
        (),
    ),
    "prepare_final_delivery": (
        ("workflow.final_delivery_valid.blocked",),
        ("workflow.final_delivery.missing",),
        (),
    ),
    "repair_final_delivery": (
        (
            "workflow.edit_manifest_current.blocked",
            "workflow.final_delivery_valid.blocked",
        ),
        ("workflow.final_delivery_valid.blocked",),
        (),
    ),
    "review_or_revise_final_delivery": (
        ("workflow.review.blocked",),
        ("workflow.review.blocked",),
        (),
    ),
    "prepare_publish_metadata": (
        ("workflow.publish_metadata.missing",),
        ("workflow.publish_metadata.missing",),
        (),
    ),
    "repair_publish_metadata": (
        (
            "workflow.brief_present.blocked",
            "workflow.publish_metadata_bound.blocked",
        ),
        ("workflow.publish_metadata_bound.blocked",),
        (),
    ),
    "approve_publish": (
        ("workflow.approval.blocked", "workflow.artifact_graph_valid.blocked"),
        ("workflow.approval.blocked",),
        ("final review pass", "publish metadata"),
    ),
    "ready_for_human_publish": (
        (),
        ("workflow.external_publish_complete.pending_human",),
        (
            "brief",
            "candidate-ranking",
            "edit-manifest",
            "final-delivery",
            "final-review",
            "generation-feasibility-review",
            "generation-packet",
            "packet-approval",
            "packet-review",
            "publish-approval",
            "publish-metadata-draft",
            "rough-cut-report",
            "shot-qc",
            "storyboard",
            "storyboard-approval",
            "storyboard-review",
        ),
    ),
}


_PROCESS_CONSOLIDATIONS = {
    "approve_storyboard": {
        "legacy_action": "approve_storyboard",
        "declarative_action": "create_generation_packet",
        "legacy_actor": "human-approver",
        "declarative_actor": "creator",
        "legacy_required_authority": {
            "requirement": "human_or_campaign",
            "approval_required": True,
        },
        "declarative_required_authority": {
            "requirement": "policy",
            "approval_required": False,
        },
        "legacy_blockers": list(_PARITY_ACTION_ROWS["approve_storyboard"][0]),
        "declarative_blockers": ["workflow.packet_present.blocked"],
        "legacy_evidence": list(_PARITY_ACTION_ROWS["approve_storyboard"][2]),
        "declarative_claim_ids": ["storyboard_approval_current"],
    }
}


def _process_consolidations() -> dict[str, object]:
    return {
        action_id: {
            key: (
                list(value)
                if isinstance(value, list)
                else dict(value)
                if isinstance(value, Mapping)
                else value
            )
            for key, value in row.items()
        }
        for action_id, row in _PROCESS_CONSOLIDATIONS.items()
    }


def _parity_action_rows() -> dict[str, object]:
    definition = default_workflow_definition()
    action_claims = {
        str(action.action_id): [str(value) for value in action.required_claim_ids]
        for action in definition.actions
    }
    if set(action_claims) != set(_PARITY_ACTION_ROWS):
        raise WorkflowContractError(
            "workflow.parity.action_catalog",
            "parity normalization does not cover the target action catalog",
        )
    return {
        action_id: {
            "legacy_blockers": list(values[0]),
            "declarative_blockers": list(values[1]),
            "legacy_evidence": list(values[2]),
            "declarative_claim_ids": action_claims[action_id],
        }
        for action_id, values in _PARITY_ACTION_ROWS.items()
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
            {"contains": "publish-metadata-draft", "reason_code": "workflow.publish_metadata.missing"},
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
        "known_blocker_reason_codes": [
            "workflow.approval.blocked",
            "workflow.artifact_graph_valid.blocked",
            "workflow.brief_present.blocked",
            "workflow.candidate_ranking_current.blocked",
            "workflow.continuity_qc.blocked",
            "workflow.continuity_qc.failed",
            "workflow.continuity_qc.missing",
            "workflow.edit_manifest_current.blocked",
            "workflow.external_publish_complete.pending_human",
            "workflow.feasibility_pass.blocked",
            "workflow.final_delivery.missing",
            "workflow.final_delivery_valid.blocked",
            "workflow.mode.preview_only",
            "workflow.packet_contract_valid.blocked",
            "workflow.packet_present.blocked",
            "workflow.publish_metadata.missing",
            "workflow.publish_metadata_bound.blocked",
            "workflow.qc.blocked",
            "workflow.review.blocked",
            "workflow.rough_cut_pass.blocked",
            "workflow.shot_qc.failed",
            "workflow.shot_qc.missing",
            "workflow.storyboard_present.blocked",
            "workflow.workspace_trusted.blocked",
        ],
        "legacy_evidence_labels": [
            "brief",
            "candidate-ranking",
            "edit-manifest",
            "feasibility pass",
            "final review pass",
            "final-delivery",
            "final-review",
            "generation readiness",
            "generation-feasibility-review",
            "generation-packet",
            "packet review pass",
            "packet-approval",
            "packet-review",
            "passing storyboard reviews",
            "publish metadata",
            "publish-approval",
            "publish-metadata-draft",
            "rough-cut-report",
            "shot-qc",
            "storyboard",
            "storyboard approval",
            "storyboard-approval",
            "storyboard-review",
            "trusted workspace observation",
        ],
        "parity_rows_by_action": _parity_action_rows(),
        "process_consolidations_by_legacy_action": _process_consolidations(),
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
        "required_authority": {
            "requirement": str(legacy_authority),
            "approval_required": legacy.approval_required,
        },
        "consumed_evidence": list(legacy.consumed_evidence),
        "prohibited_actions": sorted(legacy.prohibited_actions),
    }
    declarative_values = {
        "action": str(evaluation.recommended_action_id) if evaluation.recommended_action_id else None,
        "blockers": sorted(
            set(value.reason_code for value in item.blockers) if item is not None else ()
        ),
        "actor": item.actor_role if item is not None else None,
        "required_authority": {
            "requirement": (
                item.authority_requirement.value if item is not None else None
            ),
            "approval_required": (
                item is not None
                and item.authority_requirement != AuthorityRequirement.POLICY
            ),
        },
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
    predecessors: Sequence[WorkflowEvaluation] = (),
    normalization: Mapping[str, object] | None = None,
    explained_dimensions: Mapping[str, str] | None = None,
) -> WorkflowParityReport:
    require_target_workflow_evaluation(evaluation, predecessors=predecessors)
    if legacy.authority_effect != "none":
        raise WorkflowContractError("workflow.parity.legacy_authority", "legacy projection cannot grant authority")
    normalized = default_parity_normalization() if normalization is None else dict(normalization)
    normalization_digest = parity_normalization_sha256(normalized)
    legacy_values, declarative_values = _values(legacy, evaluation, normalized)
    if "parity.legacy_blocker_unmapped" in legacy_values["blockers"]:
        raise WorkflowContractError(
            "workflow.parity.unmapped_blocker",
            "legacy blocker text is not covered by the target normalization",
        )
    explanations = dict(explained_dimensions or {})
    action_rows = normalized["parity_rows_by_action"]
    if not isinstance(action_rows, Mapping):
        raise WorkflowContractError(
            "workflow.parity.action_catalog",
            "target parity action rows are malformed",
        )
    action_row = action_rows.get(legacy.action_type)
    if not isinstance(action_row, Mapping):
        raise WorkflowContractError(
            "workflow.parity.action_catalog",
            "legacy action has no target-owned parity row",
        )
    process_rows = normalized["process_consolidations_by_legacy_action"]
    if not isinstance(process_rows, Mapping):
        raise WorkflowContractError(
            "workflow.parity.process_catalog",
            "target process-consolidation rows are malformed",
        )
    process_row = process_rows.get(legacy.action_type)
    frontier_item = next(
        (
            value
            for value in evaluation.action_frontier
            if value.action_id == evaluation.recommended_action_id
        ),
        None,
    )
    unknown_explanations = set(explanations.values()) - ALLOWED_EXPLANATIONS
    if unknown_explanations or not set(explanations) <= set(PARITY_DIMENSIONS):
        raise WorkflowContractError("workflow.parity.explanation", "parity explanation is not allowlisted")
    if any(
        dimension not in EXPLANATION_DIMENSIONS[code]
        for dimension, code in explanations.items()
    ):
        raise WorkflowContractError(
            "workflow.parity.explanation_scope",
            "parity explanation is not valid for that semantic dimension",
        )
    differences: list[ParityDifference] = []
    unexplained: list[str] = []
    for dimension in PARITY_DIMENSIONS:
        legacy_digest = canonical_sha256(legacy_values[dimension])
        declarative_digest = canonical_sha256(declarative_values[dimension])
        if legacy_digest == declarative_digest:
            continue
        explanation = explanations.get(dimension)
        if explanation == "EXPLAINED_FRONTIER_EXPANSION":
            if (
                not isinstance(legacy_values[dimension], list)
                or not isinstance(declarative_values[dimension], list)
                or legacy_values[dimension]
                != action_row.get("legacy_blockers")
                or declarative_values[dimension]
                != action_row.get("declarative_blockers")
            ):
                raise WorkflowContractError(
                    "workflow.parity.explanation_values",
                    "blocker explanation contains an unowned reason code",
                )
        elif explanation == "EXPLAINED_BLUEPRINT_CONSOLIDATION":
            legacy_evidence = legacy_values[dimension]
            declarative_evidence = declarative_values[dimension]
            if (
                frontier_item is None
                or str(frontier_item.action_id) != legacy.action_type
                or not isinstance(legacy_evidence, list)
                or not isinstance(declarative_evidence, list)
                or legacy_evidence != action_row.get("legacy_evidence")
                or [
                    str(value)
                    for value in frontier_item.consumed_claim_ids
                ]
                != action_row.get("declarative_claim_ids")
                or any(
                    not isinstance(value, str)
                    or len(value) != 64
                    or value.lower() != value
                    or any(character not in "0123456789abcdef" for character in value)
                    for value in declarative_evidence
                )
            ):
                raise WorkflowContractError(
                    "workflow.parity.explanation_values",
                    "evidence explanation is not an exact legacy-label to digest upgrade",
                )
        elif explanation == "EXPLAINED_AUTHORITY_HARDENING":
            legacy_authority = legacy_values[dimension]
            declarative_authority = declarative_values[dimension]
            expected_keys = {"requirement", "approval_required"}
            if (
                not isinstance(legacy_authority, Mapping)
                or not isinstance(declarative_authority, Mapping)
                or set(legacy_authority) != expected_keys
                or set(declarative_authority) != expected_keys
                or legacy_authority["requirement"]
                != declarative_authority["requirement"]
                or legacy_authority["approval_required"] is not False
                or declarative_authority["approval_required"] is not True
            ):
                raise WorkflowContractError(
                    "workflow.parity.explanation_values",
                    "authority explanation is not a one-way exact authority hardening",
                )
        elif explanation == "EXPLAINED_PROCESS_CONSOLIDATION":
            if not isinstance(process_row, Mapping):
                raise WorkflowContractError(
                    "workflow.parity.explanation_values",
                    "legacy action has no target-owned process consolidation",
                )
            exact_pairs = {
                "action": ("legacy_action", "declarative_action"),
                "blockers": ("legacy_blockers", "declarative_blockers"),
                "actor": ("legacy_actor", "declarative_actor"),
                "required_authority": (
                    "legacy_required_authority",
                    "declarative_required_authority",
                ),
            }
            if dimension in exact_pairs:
                legacy_key, declarative_key = exact_pairs[dimension]
                valid_process_value = (
                    legacy_values[dimension] == process_row.get(legacy_key)
                    and declarative_values[dimension]
                    == process_row.get(declarative_key)
                )
            else:
                legacy_evidence = legacy_values[dimension]
                declarative_evidence = declarative_values[dimension]
                valid_process_value = (
                    frontier_item is not None
                    and str(frontier_item.action_id)
                    == process_row.get("declarative_action")
                    and legacy_evidence == process_row.get("legacy_evidence")
                    and [
                        str(value)
                        for value in frontier_item.consumed_claim_ids
                    ]
                    == process_row.get("declarative_claim_ids")
                    and isinstance(declarative_evidence, list)
                    and all(
                        isinstance(value, str)
                        and len(value) == 64
                        and value.lower() == value
                        and all(
                            character in "0123456789abcdef"
                            for character in value
                        )
                        for value in declarative_evidence
                    )
                )
            if not valid_process_value:
                raise WorkflowContractError(
                    "workflow.parity.explanation_values",
                    "process consolidation does not match the exact target-owned transition",
                )
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
        value.explanation_code is not None
        and (
            value.explanation_code not in ALLOWED_EXPLANATIONS
            or value.dimension
            not in EXPLANATION_DIMENSIONS[value.explanation_code]
        )
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
