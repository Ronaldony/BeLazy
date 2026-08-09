"""Target-owned declarative episode workflow definition."""

from __future__ import annotations

from collections.abc import Iterable

from video_factory.config import canonical_sha256
from video_factory.domain import HashDigest, OpaqueId

from .contracts import (
    ActionDefinition,
    ActionRisk,
    AuthorityRequirement,
    ClaimDefinition,
    GateDefinition,
    WorkflowContractError,
    WorkflowDefinition,
)


WORKFLOW_ARTIFACT_VERSION = "workflow-definition/1.0"
WORKFLOW_VERSION = "episode-production-workflow/1.0"
_CONTEXT_FIELDS = (
    "workflow_definition_sha256",
    "policy_bundle_sha256",
    "rules_bundle_sha256",
    "effective_config_sha256",
    "current_manifest_sha256",
    "evidence_graph_sha256",
)
_APPROVAL_CONTEXT_FIELDS = _CONTEXT_FIELDS
_CONTEXT_FIELDS_BY_CLAIM = {
    "artifact_graph_valid": ("rules_bundle_sha256",),
    "storyboard_approval_current": _APPROVAL_CONTEXT_FIELDS,
    "generation_approval_current": _APPROVAL_CONTEXT_FIELDS,
    "generation_mode_permits_execution": ("effective_config_sha256",),
    "workspace_trusted": ("current_manifest_sha256",),
    "publish_approval_current": _APPROVAL_CONTEXT_FIELDS,
}
_PROHIBITED = (
    "auto_transition_workflow_state",
    "auto_approve",
    "paid_external_generation",
    "publish",
    "overwrite_generated_assets",
)


def _token(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise WorkflowContractError("workflow.definition.token", f"{label} is invalid")
    if any(character.isspace() for character in value):
        raise WorkflowContractError("workflow.definition.token", f"{label} is invalid")
    return value


def _definition_identity(definition: WorkflowDefinition) -> dict[str, object]:
    return {
        "artifact_version": definition.artifact_version,
        "workflow_version": definition.workflow_version,
        "claims": [
            {
                "claim_id": str(item.claim_id),
                "dependency_claim_ids": [str(value) for value in item.dependency_claim_ids],
                "gate_id": str(item.gate_id),
            }
            for item in definition.claims
        ],
        "gates": [
            {
                "gate_id": str(item.gate_id),
                "owner": item.owner,
                "stable_reason_prefix": item.stable_reason_prefix,
                "consumed_context_fields": list(item.consumed_context_fields),
            }
            for item in definition.gates
        ],
        "actions": [
            {
                "action_id": str(item.action_id),
                "satisfies_claim_id": str(item.satisfies_claim_id),
                "required_claim_ids": [str(value) for value in item.required_claim_ids],
                "actor_role": item.actor_role,
                "capability_id": str(item.capability_id),
                "risk": item.risk.value,
                "authority_requirement": item.authority_requirement.value,
                "priority": item.priority,
                "side_effect": item.side_effect,
                "trigger_reason_codes": list(item.trigger_reason_codes),
                "prohibited_actions": list(item.prohibited_actions),
            }
            for item in definition.actions
        ],
    }


def workflow_definition_sha256(definition: WorkflowDefinition) -> HashDigest:
    return canonical_sha256(_definition_identity(definition))


def workflow_definition_to_mapping(definition: WorkflowDefinition) -> dict[str, object]:
    validate_workflow_definition(definition)
    return {
        **_definition_identity(definition),
        "workflow_id": str(definition.workflow_id),
        "definition_sha256": str(definition.definition_sha256),
    }


def _require_unique(values: Iterable[str], label: str) -> tuple[str, ...]:
    output = tuple(values)
    if len(output) != len(set(output)):
        raise WorkflowContractError("workflow.definition.duplicate", f"duplicate {label}")
    return output


def validate_workflow_definition(definition: WorkflowDefinition) -> WorkflowDefinition:
    if definition.artifact_version != WORKFLOW_ARTIFACT_VERSION:
        raise WorkflowContractError("workflow.definition.version", "unsupported definition version")
    _token(definition.workflow_version, "workflow_version")
    claim_ids = _require_unique((str(item.claim_id) for item in definition.claims), "claim_id")
    gate_ids = _require_unique((str(item.gate_id) for item in definition.gates), "gate_id")
    action_ids = _require_unique((str(item.action_id) for item in definition.actions), "action_id")
    if not claim_ids or not gate_ids or not action_ids:
        raise WorkflowContractError("workflow.definition.empty", "claims, gates and actions are required")
    claim_set = set(claim_ids)
    gate_set = set(gate_ids)
    for claim in definition.claims:
        _token(str(claim.claim_id), "claim_id")
        _token(str(claim.gate_id), "gate_id")
        if str(claim.gate_id) not in gate_set:
            raise WorkflowContractError("workflow.definition.unknown_gate", "claim references unknown gate")
        dependencies = tuple(str(value) for value in claim.dependency_claim_ids)
        _require_unique(dependencies, "claim dependency")
        if str(claim.claim_id) in dependencies or not set(dependencies) <= claim_set:
            raise WorkflowContractError("workflow.definition.dependency", "claim dependency is invalid")
    for gate in definition.gates:
        _token(gate.owner, "gate owner")
        _token(gate.stable_reason_prefix, "stable reason prefix")
        if tuple(gate.consumed_context_fields) != tuple(dict.fromkeys(gate.consumed_context_fields)):
            raise WorkflowContractError("workflow.definition.context", "gate context fields are not canonical")
        if not set(gate.consumed_context_fields) <= set(_CONTEXT_FIELDS):
            raise WorkflowContractError("workflow.definition.context", "gate consumes unknown context field")
    priorities: set[int] = set()
    for action in definition.actions:
        _token(str(action.action_id), "action_id")
        _token(str(action.capability_id), "capability_id")
        _token(action.actor_role, "actor_role")
        if str(action.satisfies_claim_id) not in claim_set:
            raise WorkflowContractError("workflow.definition.action_claim", "action satisfies unknown claim")
        required = tuple(str(value) for value in action.required_claim_ids)
        _require_unique(required, "action required claim")
        if not set(required) <= claim_set:
            raise WorkflowContractError("workflow.definition.action_claim", "action requires unknown claim")
        if action.priority < 0 or action.priority in priorities:
            raise WorkflowContractError("workflow.definition.priority", "action priority must be unique and non-negative")
        priorities.add(action.priority)
        _require_unique(action.trigger_reason_codes, "trigger reason")
        _require_unique(action.prohibited_actions, "prohibited action")

    dependencies = {
        str(item.claim_id): tuple(str(value) for value in item.dependency_claim_ids)
        for item in definition.claims
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(claim_id: str) -> None:
        if claim_id in visited:
            return
        if claim_id in visiting:
            raise WorkflowContractError("workflow.definition.cycle", "claim dependency cycle")
        visiting.add(claim_id)
        for dependency in dependencies[claim_id]:
            visit(dependency)
        visiting.remove(claim_id)
        visited.add(claim_id)

    for claim_id in claim_ids:
        visit(claim_id)

    digest = workflow_definition_sha256(definition)
    if str(definition.definition_sha256) != str(digest):
        raise WorkflowContractError("workflow.definition.digest", "definition digest mismatch")
    expected_id = f"workflow-{str(digest)[:20]}"
    if str(definition.workflow_id) != expected_id:
        raise WorkflowContractError("workflow.definition.identity", "workflow id mismatch")
    return definition


def _claim(claim_id: str, dependencies: tuple[str, ...] = ()) -> ClaimDefinition:
    return ClaimDefinition(
        claim_id=OpaqueId(claim_id),
        dependency_claim_ids=tuple(OpaqueId(value) for value in dependencies),
        gate_id=OpaqueId(f"gate.{claim_id}"),
    )


def _gate(claim_id: str, owner: str) -> GateDefinition:
    return GateDefinition(
        gate_id=OpaqueId(f"gate.{claim_id}"),
        owner=owner,
        stable_reason_prefix=f"workflow.{claim_id}",
        consumed_context_fields=_CONTEXT_FIELDS_BY_CLAIM.get(claim_id, ()),
    )


def _action(
    action_id: str,
    claim_id: str,
    required: tuple[str, ...],
    actor: str,
    capability: str,
    risk: ActionRisk,
    authority: AuthorityRequirement,
    priority: int,
    *,
    side_effect: bool = False,
    triggers: tuple[str, ...] = (),
) -> ActionDefinition:
    return ActionDefinition(
        action_id=OpaqueId(action_id),
        satisfies_claim_id=OpaqueId(claim_id),
        required_claim_ids=tuple(OpaqueId(value) for value in required),
        actor_role=actor,
        capability_id=OpaqueId(capability),
        risk=risk,
        authority_requirement=authority,
        priority=priority,
        side_effect=side_effect,
        trigger_reason_codes=triggers,
        prohibited_actions=_PROHIBITED,
    )


def default_workflow_definition() -> WorkflowDefinition:
    """Return the exact target-owned W04 definition.

    Packet review and feasibility, plus final review and metadata preparation,
    intentionally share dependencies so the evaluator can expose a parallel
    frontier while retaining the legacy recommendation through priority.
    """

    specs = (
        ("artifact_graph_valid", (), "artifact-owner"),
        ("brief_present", ("artifact_graph_valid",), "creator"),
        ("storyboard_present", ("brief_present",), "creator"),
        ("storyboard_review_pass", ("storyboard_present",), "reviewer"),
        ("storyboard_approval_current", ("storyboard_review_pass",), "human-approver"),
        ("packet_present", ("storyboard_approval_current",), "creator"),
        ("packet_contract_valid", ("packet_present",), "creator"),
        ("packet_review_pass", ("packet_contract_valid",), "reviewer"),
        ("feasibility_pass", ("packet_contract_valid",), "independent-reviewer"),
        ("generation_approval_current", ("packet_review_pass", "feasibility_pass"), "human-approver"),
        ("generation_mode_permits_execution", ("generation_approval_current",), "human-operator"),
        ("workspace_trusted", ("generation_mode_permits_execution",), "workspace-authority"),
        ("shot_qc_pass", ("workspace_trusted",), "human-or-channel-tool"),
        ("continuity_qc_pass", ("shot_qc_pass",), "qc-tool-or-reviewer"),
        ("candidate_ranking_current", ("continuity_qc_pass",), "qc-tool"),
        ("edit_manifest_current", ("candidate_ranking_current",), "human-editor"),
        ("rough_cut_pass", ("edit_manifest_current",), "editor"),
        ("final_delivery_present", ("rough_cut_pass",), "editor"),
        ("final_delivery_valid", ("final_delivery_present",), "editor"),
        ("final_review_pass", ("final_delivery_valid",), "reviewer"),
        ("publish_metadata_present", ("final_delivery_valid",), "creator"),
        ("publish_metadata_bound", ("publish_metadata_present",), "creator"),
        ("publish_approval_current", ("final_review_pass", "publish_metadata_bound"), "human-approver"),
        ("external_publish_complete", ("publish_approval_current",), "human-publisher"),
    )
    claims = tuple(_claim(name, dependencies) for name, dependencies, _ in specs)
    gates = tuple(_gate(name, owner) for name, _, owner in specs)
    actions = (
        _action("resolve_artifact_graph", "artifact_graph_valid", (), "artifact-owner", "repair_artifact_graph", ActionRisk.R0, AuthorityRequirement.POLICY, 0),
        _action("create_brief", "brief_present", ("artifact_graph_valid",), "creator", "write_brief", ActionRisk.R1, AuthorityRequirement.POLICY, 1),
        _action("create_storyboard", "storyboard_present", ("brief_present",), "creator", "write_storyboard", ActionRisk.R1, AuthorityRequirement.POLICY, 2),
        _action("review_or_revise_storyboard", "storyboard_review_pass", ("storyboard_present",), "reviewer", "review_storyboard", ActionRisk.R0, AuthorityRequirement.POLICY, 3),
        _action("approve_storyboard", "storyboard_approval_current", ("storyboard_review_pass",), "human-approver", "storyboard_approval", ActionRisk.R0, AuthorityRequirement.HUMAN_OR_CAMPAIGN, 4),
        _action("create_generation_packet", "packet_present", ("storyboard_approval_current",), "creator", "write_generation_packet", ActionRisk.R1, AuthorityRequirement.POLICY, 5),
        _action("rebuild_generation_packet", "packet_contract_valid", ("packet_present",), "creator", "write_generation_packet", ActionRisk.R1, AuthorityRequirement.POLICY, 6),
        _action("review_or_revise_generation_packet", "packet_review_pass", ("packet_contract_valid",), "reviewer", "review_generation_packet", ActionRisk.R0, AuthorityRequirement.POLICY, 7),
        _action("review_generation_feasibility", "feasibility_pass", ("packet_contract_valid",), "independent-reviewer", "review_generation_feasibility", ActionRisk.R0, AuthorityRequirement.POLICY, 8),
        _action("approve_generation", "generation_approval_current", ("packet_review_pass", "feasibility_pass"), "human-approver", "generation_approval", ActionRisk.R0, AuthorityRequirement.HUMAN_OR_CAMPAIGN, 9),
        _action("preview_complete", "generation_mode_permits_execution", ("generation_approval_current",), "human-operator", "select_production_profile", ActionRisk.R0, AuthorityRequirement.POLICY, 10, triggers=("workflow.mode.preview_only",)),
        _action("reconcile_workspace", "workspace_trusted", ("generation_mode_permits_execution",), "workspace-authority", "reconcile_workspace", ActionRisk.R4, AuthorityRequirement.TWO_INDEPENDENT_HUMANS, 11, side_effect=True),
        _action("run_external_generation", "shot_qc_pass", ("workspace_trusted",), "human-or-channel-tool", "paid_external_generation", ActionRisk.R2, AuthorityRequirement.STANDING_OR_ONE_HUMAN, 12, side_effect=True, triggers=("workflow.shot_qc.missing",)),
        _action("remediate_or_repeat_shot_qc", "shot_qc_pass", ("workspace_trusted",), "human-or-channel-tool", "paid_external_generation", ActionRisk.R2, AuthorityRequirement.STANDING_OR_ONE_HUMAN, 13, side_effect=True, triggers=("workflow.shot_qc.failed", "workflow.shot_qc.stale", "workflow.shot_qc.ambiguous")),
        _action("run_continuity_qc", "continuity_qc_pass", ("shot_qc_pass",), "qc-tool-or-reviewer", "review_cross_shot_continuity", ActionRisk.R0, AuthorityRequirement.POLICY, 14, triggers=("workflow.continuity_qc.missing",)),
        _action("remediate_continuity_qc", "continuity_qc_pass", ("shot_qc_pass",), "qc-tool-or-reviewer", "review_cross_shot_continuity", ActionRisk.R0, AuthorityRequirement.POLICY, 15, triggers=("workflow.continuity_qc.failed", "workflow.continuity_qc.stale", "workflow.continuity_qc.ambiguous")),
        _action("rank_generation_candidates", "candidate_ranking_current", ("continuity_qc_pass",), "qc-tool", "rank_candidates", ActionRisk.R0, AuthorityRequirement.POLICY, 16),
        _action("select_edit_inputs", "edit_manifest_current", ("candidate_ranking_current",), "human-editor", "prepare_edit_manifest", ActionRisk.R0, AuthorityRequirement.HUMAN_OR_CAMPAIGN, 17),
        _action("assemble_or_repair_rough_cut", "rough_cut_pass", ("edit_manifest_current",), "editor", "assemble_rough_cut", ActionRisk.R1, AuthorityRequirement.POLICY, 18),
        _action("prepare_final_delivery", "final_delivery_present", ("rough_cut_pass",), "editor", "prepare_final_delivery", ActionRisk.R1, AuthorityRequirement.POLICY, 19, triggers=("workflow.final_delivery.missing",)),
        _action("repair_final_delivery", "final_delivery_valid", ("final_delivery_present",), "editor", "prepare_final_delivery", ActionRisk.R1, AuthorityRequirement.POLICY, 20),
        _action("review_or_revise_final_delivery", "final_review_pass", ("final_delivery_valid",), "reviewer", "review_final_delivery", ActionRisk.R0, AuthorityRequirement.POLICY, 21),
        _action("prepare_publish_metadata", "publish_metadata_present", ("final_delivery_valid",), "creator", "draft_publish_metadata", ActionRisk.R1, AuthorityRequirement.POLICY, 22, triggers=("workflow.publish_metadata.missing",)),
        _action("repair_publish_metadata", "publish_metadata_bound", ("publish_metadata_present",), "creator", "draft_publish_metadata", ActionRisk.R1, AuthorityRequirement.POLICY, 23),
        _action("approve_publish", "publish_approval_current", ("final_review_pass", "publish_metadata_bound"), "human-approver", "publish_approval", ActionRisk.R0, AuthorityRequirement.HUMAN_OR_CAMPAIGN, 24),
        _action("ready_for_human_publish", "external_publish_complete", ("publish_approval_current",), "human-publisher", "publish", ActionRisk.R3, AuthorityRequirement.HUMAN_OR_CAMPAIGN, 25, side_effect=True),
    )
    provisional = WorkflowDefinition(
        artifact_version=WORKFLOW_ARTIFACT_VERSION,
        workflow_id=OpaqueId("pending"),
        workflow_version=WORKFLOW_VERSION,
        definition_sha256=HashDigest("0" * 64),
        claims=claims,
        gates=gates,
        actions=actions,
    )
    digest = workflow_definition_sha256(provisional)
    result = WorkflowDefinition(
        artifact_version=provisional.artifact_version,
        workflow_id=OpaqueId(f"workflow-{str(digest)[:20]}"),
        workflow_version=provisional.workflow_version,
        definition_sha256=digest,
        claims=claims,
        gates=gates,
        actions=actions,
    )
    return validate_workflow_definition(result)


def require_target_workflow_definition(definition: WorkflowDefinition) -> WorkflowDefinition:
    validate_workflow_definition(definition)
    expected = default_workflow_definition()
    if definition != expected:
        raise WorkflowContractError(
            "workflow.definition.not_target_owned",
            "authority-bearing evaluation requires the exact target-owned definition",
        )
    return definition
