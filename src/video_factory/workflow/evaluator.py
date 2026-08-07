"""Deterministic DAG evaluation and transitive incremental invalidation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from video_factory.approvals import GateContext, gate_context_sha256
from video_factory.config import canonical_sha256
from video_factory.domain import HashDigest, OpaqueId

from .contracts import (
    ActionBlocker,
    ActionDefinition,
    ActionFrontierItem,
    ExecutableProductionPlan,
    GateResult,
    GateStatus,
    MaterialContextSeed,
    WorkflowContractError,
    WorkflowDefinition,
    WorkflowEvaluation,
)
from .definition import (
    default_workflow_definition,
    require_target_workflow_definition,
    validate_workflow_definition,
)


WORKFLOW_EVALUATION_VERSION = "workflow-evaluation/1.0"
EXECUTABLE_PLAN_VERSION = "executable-production-plan/1.0"
GATE_RESULT_VERSION = "gate-result/1.0"


def _digest(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or value.lower() != value
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise WorkflowContractError("workflow.digest.invalid", f"{label} is not a lowercase SHA-256")
    return value


def material_context_to_mapping(context: MaterialContextSeed) -> dict[str, str]:
    output = {
        "workflow_definition_sha256": str(context.workflow_definition_sha256),
        "policy_bundle_sha256": str(context.policy_bundle_sha256),
        "rules_bundle_sha256": str(context.rules_bundle_sha256),
        "effective_config_sha256": str(context.effective_config_sha256),
        "current_manifest_sha256": str(context.current_manifest_sha256),
        "evidence_graph_sha256": str(context.evidence_graph_sha256),
    }
    for field, value in output.items():
        _digest(value, field)
    return output


def material_context_sha256(context: MaterialContextSeed) -> HashDigest:
    return canonical_sha256(material_context_to_mapping(context))


def gate_result_to_mapping(result: GateResult) -> dict[str, object]:
    validate_gate_result(result)
    return {
        "artifact_version": result.artifact_version,
        "gate_id": str(result.gate_id),
        "status": result.status.value,
        "reason_codes": list(result.reason_codes),
        "messages": list(result.messages),
        "evidence_sha256s": [str(value) for value in result.evidence_sha256s],
        "result_sha256": str(result.result_sha256),
    }


def _gate_result_identity(
    artifact_version: str,
    gate_id: str,
    status: GateStatus,
    reason_codes: Sequence[str],
    messages: Sequence[str],
    evidence_sha256s: Sequence[str],
) -> dict[str, object]:
    return {
        "artifact_version": artifact_version,
        "gate_id": gate_id,
        "status": status.value,
        "reason_codes": list(reason_codes),
        "messages": list(messages),
        "evidence_sha256s": list(evidence_sha256s),
    }


def build_gate_result(
    gate_id: str,
    status: GateStatus,
    *,
    reason_codes: Sequence[str] = (),
    messages: Sequence[str] = (),
    evidence_sha256s: Sequence[str] = (),
) -> GateResult:
    if status is GateStatus.PASS and (reason_codes or messages):
        raise WorkflowContractError("workflow.gate.pass_reasons", "passing gate cannot contain blockers")
    if status is not GateStatus.PASS and not reason_codes:
        raise WorkflowContractError("workflow.gate.reason_missing", "non-passing gate requires a stable reason")
    normalized_reasons = tuple(reason_codes)
    normalized_messages = tuple(messages)
    normalized_evidence = tuple(sorted(set(evidence_sha256s)))
    identity = _gate_result_identity(
        GATE_RESULT_VERSION, gate_id, status, normalized_reasons, normalized_messages, normalized_evidence
    )
    result = GateResult(
        artifact_version=GATE_RESULT_VERSION,
        gate_id=OpaqueId(gate_id),
        status=status,
        reason_codes=normalized_reasons,
        messages=normalized_messages,
        evidence_sha256s=tuple(HashDigest(value) for value in normalized_evidence),
        result_sha256=canonical_sha256(identity),
    )
    return validate_gate_result(result)


def validate_gate_result(result: GateResult) -> GateResult:
    if result.artifact_version != GATE_RESULT_VERSION:
        raise WorkflowContractError("workflow.gate.version", "unsupported gate result version")
    if not str(result.gate_id) or any(character.isspace() for character in str(result.gate_id)):
        raise WorkflowContractError("workflow.gate.id", "gate id is invalid")
    if len(result.reason_codes) != len(set(result.reason_codes)):
        raise WorkflowContractError("workflow.gate.reason_duplicate", "gate reasons are duplicated")
    if result.status is GateStatus.PASS and (result.reason_codes or result.messages):
        raise WorkflowContractError("workflow.gate.pass_reasons", "passing gate cannot contain blockers")
    if result.status is not GateStatus.PASS and not result.reason_codes:
        raise WorkflowContractError("workflow.gate.reason_missing", "non-passing gate requires a stable reason")
    evidence = tuple(str(value) for value in result.evidence_sha256s)
    if evidence != tuple(sorted(set(evidence))):
        raise WorkflowContractError("workflow.gate.evidence_order", "gate evidence is not sorted and unique")
    for value in evidence:
        _digest(value, "gate evidence")
    expected = canonical_sha256(
        _gate_result_identity(
            result.artifact_version,
            str(result.gate_id),
            result.status,
            result.reason_codes,
            result.messages,
            evidence,
        )
    )
    if str(expected) != str(result.result_sha256):
        raise WorkflowContractError("workflow.gate.digest", "gate result digest mismatch")
    return result


def _blocker_mapping(blocker: ActionBlocker) -> dict[str, str]:
    return {
        "claim_id": str(blocker.claim_id),
        "gate_id": str(blocker.gate_id),
        "reason_code": blocker.reason_code,
        "message": blocker.message,
    }


def _frontier_identity(item: ActionFrontierItem) -> dict[str, object]:
    return {
        "action_id": str(item.action_id),
        "plan_sha256": str(item.plan_sha256),
        "actor_role": item.actor_role,
        "capability_id": str(item.capability_id),
        "risk": item.risk.value,
        "authority_requirement": item.authority_requirement.value,
        "consumed_claim_ids": [str(value) for value in item.consumed_claim_ids],
        "consumed_evidence_sha256s": [str(value) for value in item.consumed_evidence_sha256s],
        "blockers": [_blocker_mapping(value) for value in item.blockers],
        "prohibited_actions": list(item.prohibited_actions),
        "authority_effect": item.authority_effect,
    }


def workflow_evaluation_to_mapping(evaluation: WorkflowEvaluation) -> dict[str, object]:
    validate_workflow_evaluation(evaluation)
    return {
        "artifact_version": evaluation.artifact_version,
        "evaluation_id": str(evaluation.evaluation_id),
        "evaluation_sha256": str(evaluation.evaluation_sha256),
        "workflow_id": str(evaluation.workflow_id),
        "workflow_definition_sha256": str(evaluation.workflow_definition_sha256),
        "material_context": material_context_to_mapping(evaluation.material_context),
        "material_context_sha256": str(evaluation.material_context_sha256),
        "gate_results": [gate_result_to_mapping(value) for value in evaluation.gate_results],
        "gate_input_sha256s": [
            {"gate_id": str(gate_id), "sha256": str(digest)}
            for gate_id, digest in evaluation.gate_input_sha256s
        ],
        "satisfied_claim_ids": [str(value) for value in evaluation.satisfied_claim_ids],
        "blockers": [_blocker_mapping(value) for value in evaluation.blockers],
        "action_frontier": [_frontier_identity(value) for value in evaluation.action_frontier],
        "recommended_action_id": (
            str(evaluation.recommended_action_id)
            if evaluation.recommended_action_id is not None
            else None
        ),
        "invalidated_claim_ids": [str(value) for value in evaluation.invalidated_claim_ids],
        "reused_claim_ids": [str(value) for value in evaluation.reused_claim_ids],
        "authority_effect": evaluation.authority_effect,
    }


def _evaluation_identity(evaluation: WorkflowEvaluation) -> dict[str, object]:
    mapping = workflow_evaluation_to_mapping.__wrapped__(evaluation) if hasattr(workflow_evaluation_to_mapping, "__wrapped__") else None
    if mapping is not None:  # pragma: no cover - defensive for decorators
        return mapping
    return {
        "artifact_version": evaluation.artifact_version,
        "workflow_id": str(evaluation.workflow_id),
        "workflow_definition_sha256": str(evaluation.workflow_definition_sha256),
        "material_context": material_context_to_mapping(evaluation.material_context),
        "material_context_sha256": str(evaluation.material_context_sha256),
        "gate_results": [
            {
                **_gate_result_identity(
                    value.artifact_version, str(value.gate_id), value.status, value.reason_codes, value.messages,
                    tuple(str(item) for item in value.evidence_sha256s),
                ),
                "result_sha256": str(value.result_sha256),
            }
            for value in evaluation.gate_results
        ],
        "gate_input_sha256s": [
            {"gate_id": str(gate_id), "sha256": str(digest)}
            for gate_id, digest in evaluation.gate_input_sha256s
        ],
        "satisfied_claim_ids": [str(value) for value in evaluation.satisfied_claim_ids],
        "blockers": [_blocker_mapping(value) for value in evaluation.blockers],
        "action_frontier": [_frontier_identity(value) for value in evaluation.action_frontier],
        "recommended_action_id": (
            str(evaluation.recommended_action_id)
            if evaluation.recommended_action_id is not None
            else None
        ),
        "invalidated_claim_ids": [str(value) for value in evaluation.invalidated_claim_ids],
        "reused_claim_ids": [str(value) for value in evaluation.reused_claim_ids],
        "authority_effect": evaluation.authority_effect,
    }


def _plan_identity(
    definition: WorkflowDefinition,
    context: MaterialContextSeed,
    action: ActionDefinition,
    consumed_evidence: Sequence[str],
) -> dict[str, object]:
    return {
        "artifact_version": EXECUTABLE_PLAN_VERSION,
        "workflow_definition_sha256": str(definition.definition_sha256),
        "material_context": material_context_to_mapping(context),
        "action_id": str(action.action_id),
        "capability_id": str(action.capability_id),
        "actor_role": action.actor_role,
        "risk": action.risk.value,
        "authority_requirement": action.authority_requirement.value,
        "side_effect": action.side_effect,
        "consumed_claim_ids": [str(value) for value in action.required_claim_ids],
        "consumed_evidence_sha256s": list(consumed_evidence),
        "prohibited_actions": list(action.prohibited_actions),
        "authority_effect": "none",
    }


def _matching_action_reason(action: ActionDefinition, reasons: set[str]) -> bool:
    if not action.trigger_reason_codes:
        return True
    return any(
        reason == trigger or reason.startswith(trigger + ".")
        for trigger in action.trigger_reason_codes
        for reason in reasons
    )


def _transitive_invalidated_claims(
    definition: WorkflowDefinition,
    changed_gate_ids: set[str],
) -> set[str]:
    invalidated = {
        str(claim.claim_id)
        for claim in definition.claims
        if str(claim.gate_id) in changed_gate_ids
    }
    changed = True
    while changed:
        changed = False
        for claim in definition.claims:
            claim_id = str(claim.claim_id)
            if claim_id in invalidated:
                continue
            if any(str(dependency) in invalidated for dependency in claim.dependency_claim_ids):
                invalidated.add(claim_id)
                changed = True
    return invalidated


def evaluate_workflow(
    definition: WorkflowDefinition,
    gate_results: Sequence[GateResult],
    material_context: MaterialContextSeed,
    *,
    previous: WorkflowEvaluation | None = None,
) -> WorkflowEvaluation:
    """Evaluate every gate without short-circuiting and return the full frontier."""

    validate_workflow_definition(definition)
    context_mapping = material_context_to_mapping(material_context)
    if context_mapping["workflow_definition_sha256"] != str(definition.definition_sha256):
        raise WorkflowContractError("workflow.context.definition", "material context is bound to another workflow definition")
    supplied: dict[str, GateResult] = {}
    for result in gate_results:
        validate_gate_result(result)
        gate_id = str(result.gate_id)
        if gate_id in supplied:
            raise WorkflowContractError("workflow.gate.duplicate", "duplicate gate result")
        supplied[gate_id] = result
    known_gate_ids = {str(value.gate_id) for value in definition.gates}
    if not set(supplied) <= known_gate_ids:
        raise WorkflowContractError("workflow.gate.unknown", "result references an unknown gate")
    complete: dict[str, GateResult] = dict(supplied)
    for gate_id in sorted(known_gate_ids - set(supplied)):
        complete[gate_id] = build_gate_result(
            gate_id,
            GateStatus.UNKNOWN,
            reason_codes=("workflow.gate.missing",),
            messages=("required gate result is missing",),
        )

    ordered_results = tuple(complete[str(gate.gate_id)] for gate in definition.gates)
    input_digests = tuple(
        (result.gate_id, result.result_sha256) for result in ordered_results
    )
    claim_by_id = {str(value.claim_id): value for value in definition.claims}
    satisfied: set[str] = set()
    blockers_by_claim: dict[str, tuple[ActionBlocker, ...]] = {}
    pending = list(definition.claims)
    while pending:
        progressed = False
        for claim in tuple(pending):
            dependencies = tuple(str(value) for value in claim.dependency_claim_ids)
            unresolved = [value for value in dependencies if value not in satisfied and value not in blockers_by_claim]
            if unresolved:
                continue
            result = complete[str(claim.gate_id)]
            dependency_failures = [value for value in dependencies if value not in satisfied]
            if result.status is GateStatus.PASS and not dependency_failures:
                satisfied.add(str(claim.claim_id))
                blockers_by_claim[str(claim.claim_id)] = ()
            else:
                values: list[ActionBlocker] = []
                if result.status is not GateStatus.PASS:
                    for index, reason in enumerate(result.reason_codes):
                        message = result.messages[index] if index < len(result.messages) else reason
                        values.append(ActionBlocker(claim.claim_id, claim.gate_id, reason, message))
                for dependency in dependency_failures:
                    values.append(
                        ActionBlocker(
                            claim.claim_id,
                            claim.gate_id,
                            "workflow.claim.dependency_blocked",
                            f"dependency {dependency} is not satisfied",
                        )
                    )
                blockers_by_claim[str(claim.claim_id)] = tuple(values)
            pending.remove(claim)
            progressed = True
        if not progressed:  # validated DAG makes this unreachable
            raise WorkflowContractError("workflow.definition.order", "claim graph cannot be evaluated")

    all_blockers = tuple(
        blocker
        for claim in definition.claims
        for blocker in blockers_by_claim[str(claim.claim_id)]
    )
    frontier: list[tuple[int, ActionFrontierItem]] = []
    for action in definition.actions:
        target = str(action.satisfies_claim_id)
        if target in satisfied:
            continue
        required = tuple(str(value) for value in action.required_claim_ids)
        if not all(value in satisfied for value in required):
            continue
        target_blockers = blockers_by_claim[target]
        reasons = {value.reason_code for value in target_blockers}
        if not _matching_action_reason(action, reasons):
            continue
        consumed = sorted(
            {
                str(complete[str(claim_by_id[value].gate_id)].result_sha256)
                for value in required
            }
        )
        plan_sha = canonical_sha256(_plan_identity(definition, material_context, action, consumed))
        frontier.append(
            (
                action.priority,
                ActionFrontierItem(
                    action_id=action.action_id,
                    plan_sha256=plan_sha,
                    actor_role=action.actor_role,
                    capability_id=action.capability_id,
                    risk=action.risk,
                    authority_requirement=action.authority_requirement,
                    consumed_claim_ids=action.required_claim_ids,
                    consumed_evidence_sha256s=tuple(HashDigest(value) for value in consumed),
                    blockers=target_blockers,
                    prohibited_actions=action.prohibited_actions,
                    authority_effect="none",
                ),
            )
        )
    frontier.sort(key=lambda item: (item[0], str(item[1].action_id)))
    frontier_values = tuple(value for _, value in frontier)

    all_claim_ids = {str(value.claim_id) for value in definition.claims}
    if previous is None:
        invalidated = all_claim_ids
        reused: set[str] = set()
    else:
        validate_workflow_evaluation(previous)
        if (
            str(previous.workflow_definition_sha256) != str(definition.definition_sha256)
            or str(previous.workflow_id) != str(definition.workflow_id)
        ):
            raise WorkflowContractError("workflow.incremental.definition", "previous evaluation uses another definition")
        previous_inputs = {str(key): str(value) for key, value in previous.gate_input_sha256s}
        current_inputs = {str(key): str(value) for key, value in input_digests}
        changed_gates = {
            gate_id for gate_id in known_gate_ids if previous_inputs.get(gate_id) != current_inputs.get(gate_id)
        }
        if previous.material_context != material_context:
            changed_gates = set(known_gate_ids)
        invalidated = _transitive_invalidated_claims(definition, changed_gates)
        reused = all_claim_ids - invalidated

    provisional = WorkflowEvaluation(
        artifact_version=WORKFLOW_EVALUATION_VERSION,
        evaluation_id=OpaqueId("pending"),
        evaluation_sha256=HashDigest("0" * 64),
        workflow_id=definition.workflow_id,
        workflow_definition_sha256=definition.definition_sha256,
        material_context=material_context,
        material_context_sha256=material_context_sha256(material_context),
        gate_results=ordered_results,
        gate_input_sha256s=input_digests,
        satisfied_claim_ids=tuple(OpaqueId(value) for value in sorted(satisfied)),
        blockers=all_blockers,
        action_frontier=frontier_values,
        recommended_action_id=(frontier_values[0].action_id if frontier_values else None),
        invalidated_claim_ids=tuple(OpaqueId(value) for value in sorted(invalidated)),
        reused_claim_ids=tuple(OpaqueId(value) for value in sorted(reused)),
        authority_effect="none",
    )
    digest = canonical_sha256(_evaluation_identity(provisional))
    result = WorkflowEvaluation(
        artifact_version=provisional.artifact_version,
        evaluation_id=OpaqueId(f"workflow-evaluation-{str(digest)[:20]}"),
        evaluation_sha256=digest,
        workflow_id=provisional.workflow_id,
        workflow_definition_sha256=provisional.workflow_definition_sha256,
        material_context=provisional.material_context,
        material_context_sha256=provisional.material_context_sha256,
        gate_results=provisional.gate_results,
        gate_input_sha256s=provisional.gate_input_sha256s,
        satisfied_claim_ids=provisional.satisfied_claim_ids,
        blockers=provisional.blockers,
        action_frontier=provisional.action_frontier,
        recommended_action_id=provisional.recommended_action_id,
        invalidated_claim_ids=provisional.invalidated_claim_ids,
        reused_claim_ids=provisional.reused_claim_ids,
        authority_effect="none",
    )
    return validate_workflow_evaluation(result)


def validate_workflow_evaluation(evaluation: WorkflowEvaluation) -> WorkflowEvaluation:
    if evaluation.artifact_version != WORKFLOW_EVALUATION_VERSION:
        raise WorkflowContractError("workflow.evaluation.version", "unsupported evaluation version")
    if evaluation.authority_effect != "none":
        raise WorkflowContractError("workflow.evaluation.authority", "workflow evaluation cannot grant authority")
    if str(material_context_sha256(evaluation.material_context)) != str(evaluation.material_context_sha256):
        raise WorkflowContractError("workflow.evaluation.context", "material context digest mismatch")
    if str(evaluation.material_context.workflow_definition_sha256) != str(evaluation.workflow_definition_sha256):
        raise WorkflowContractError("workflow.evaluation.context", "workflow digest is not bound to material context")
    for result in evaluation.gate_results:
        validate_gate_result(result)
    if tuple(str(value) for value in evaluation.satisfied_claim_ids) != tuple(
        sorted(set(str(value) for value in evaluation.satisfied_claim_ids))
    ):
        raise WorkflowContractError("workflow.evaluation.claim_order", "satisfied claims are not sorted and unique")
    if set(evaluation.invalidated_claim_ids) & set(evaluation.reused_claim_ids):
        raise WorkflowContractError("workflow.evaluation.invalidation", "claim cannot be both reused and invalidated")
    digest = canonical_sha256(_evaluation_identity(evaluation))
    if str(digest) != str(evaluation.evaluation_sha256):
        raise WorkflowContractError("workflow.evaluation.digest", "evaluation digest mismatch")
    if str(evaluation.evaluation_id) != f"workflow-evaluation-{str(digest)[:20]}":
        raise WorkflowContractError("workflow.evaluation.identity", "evaluation id mismatch")
    return evaluation


def workflow_semantic_projection(evaluation: WorkflowEvaluation) -> dict[str, object]:
    """Return fields that must match between incremental and clean evaluation."""

    validate_workflow_evaluation(evaluation)
    return {
        "gate_results": [gate_result_to_mapping(value) for value in evaluation.gate_results],
        "satisfied_claim_ids": [str(value) for value in evaluation.satisfied_claim_ids],
        "blockers": [_blocker_mapping(value) for value in evaluation.blockers],
        "action_frontier": [_frontier_identity(value) for value in evaluation.action_frontier],
        "recommended_action_id": (
            str(evaluation.recommended_action_id) if evaluation.recommended_action_id else None
        ),
    }


def require_target_workflow_evaluation(
    evaluation: WorkflowEvaluation,
) -> WorkflowEvaluation:
    """Recompute target-owned semantics before a result can feed a plan.

    A canonical digest proves only the identity of the supplied document.  It
    does not prove that its blockers or frontier were actually derived from
    the bundled gate results.  Production consumers therefore compare the
    complete semantic projection with a clean target-DAG evaluation.
    """

    validate_workflow_evaluation(evaluation)
    definition = default_workflow_definition()
    require_target_workflow_definition(definition)
    if (
        evaluation.workflow_id != definition.workflow_id
        or evaluation.workflow_definition_sha256 != definition.definition_sha256
    ):
        raise WorkflowContractError(
            "workflow.evaluation.not_target_owned",
            "evaluation is not bound to the exact target workflow",
        )
    clean = evaluate_workflow(
        definition,
        evaluation.gate_results,
        evaluation.material_context,
    )
    if (
        evaluation.gate_input_sha256s != clean.gate_input_sha256s
        or workflow_semantic_projection(evaluation)
        != workflow_semantic_projection(clean)
    ):
        raise WorkflowContractError(
            "workflow.evaluation.semantic_rebound",
            "evaluation semantics do not match a clean target-DAG recomputation",
        )
    return evaluation


def executable_plan_to_mapping(plan: ExecutableProductionPlan) -> dict[str, object]:
    return {
        "artifact_version": plan.artifact_version,
        "plan_id": str(plan.plan_id),
        "plan_sha256": str(plan.plan_sha256),
        "workflow_evaluation_sha256": str(plan.workflow_evaluation_sha256),
        "workflow_definition_sha256": str(plan.workflow_definition_sha256),
        "action_id": str(plan.action_id),
        "capability_id": str(plan.capability_id),
        "actor_role": plan.actor_role,
        "risk": plan.risk.value,
        "authority_requirement": plan.authority_requirement.value,
        "side_effect": plan.side_effect,
        "gate_context": {
            "workflow_definition_sha256": str(plan.gate_context.workflow_definition_sha256),
            "policy_bundle_sha256": str(plan.gate_context.policy_bundle_sha256),
            "rules_bundle_sha256": str(plan.gate_context.rules_bundle_sha256),
            "effective_config_sha256": str(plan.gate_context.effective_config_sha256),
            "current_manifest_sha256": str(plan.gate_context.current_manifest_sha256),
            "evidence_graph_sha256": str(plan.gate_context.evidence_graph_sha256),
            "executable_plan_sha256": str(plan.gate_context.executable_plan_sha256),
        },
        "consumed_claim_ids": [str(value) for value in plan.consumed_claim_ids],
        "consumed_evidence_sha256s": [str(value) for value in plan.consumed_evidence_sha256s],
        "prohibited_actions": list(plan.prohibited_actions),
        "authority_effect": plan.authority_effect,
    }


def build_executable_production_plan(
    definition: WorkflowDefinition,
    evaluation: WorkflowEvaluation,
    action_id: str,
) -> ExecutableProductionPlan:
    """Finalize one frontier item into its own seven-digest GateContext."""

    require_target_workflow_definition(definition)
    require_target_workflow_evaluation(evaluation)
    if str(evaluation.workflow_definition_sha256) != str(definition.definition_sha256):
        raise WorkflowContractError("workflow.plan.definition", "evaluation uses another definition")
    item = next((value for value in evaluation.action_frontier if str(value.action_id) == action_id), None)
    if item is None:
        raise WorkflowContractError("workflow.plan.not_frontier", "action is not in the evaluated frontier")
    action = next(value for value in definition.actions if str(value.action_id) == action_id)
    context = GateContext(
        workflow_definition_sha256=evaluation.material_context.workflow_definition_sha256,
        policy_bundle_sha256=evaluation.material_context.policy_bundle_sha256,
        rules_bundle_sha256=evaluation.material_context.rules_bundle_sha256,
        effective_config_sha256=evaluation.material_context.effective_config_sha256,
        current_manifest_sha256=evaluation.material_context.current_manifest_sha256,
        evidence_graph_sha256=evaluation.material_context.evidence_graph_sha256,
        executable_plan_sha256=item.plan_sha256,
    )
    if str(gate_context_sha256(context)) == str(item.plan_sha256):
        raise WorkflowContractError("workflow.plan.digest_cycle", "plan and context digest domains collided")
    plan = ExecutableProductionPlan(
        artifact_version=EXECUTABLE_PLAN_VERSION,
        plan_id=OpaqueId(f"production-plan-{str(item.plan_sha256)[:20]}"),
        plan_sha256=item.plan_sha256,
        workflow_evaluation_sha256=evaluation.evaluation_sha256,
        workflow_definition_sha256=definition.definition_sha256,
        action_id=item.action_id,
        capability_id=item.capability_id,
        actor_role=item.actor_role,
        risk=item.risk,
        authority_requirement=item.authority_requirement,
        side_effect=action.side_effect,
        gate_context=context,
        consumed_claim_ids=item.consumed_claim_ids,
        consumed_evidence_sha256s=item.consumed_evidence_sha256s,
        prohibited_actions=item.prohibited_actions,
        authority_effect="none",
    )
    return validate_executable_production_plan(definition, plan)


def validate_executable_production_plan(
    definition: WorkflowDefinition,
    plan: ExecutableProductionPlan,
) -> ExecutableProductionPlan:
    require_target_workflow_definition(definition)
    if plan.artifact_version != EXECUTABLE_PLAN_VERSION or plan.authority_effect != "none":
        raise WorkflowContractError("workflow.plan.contract", "invalid executable plan contract")
    action = next((value for value in definition.actions if value.action_id == plan.action_id), None)
    if action is None:
        raise WorkflowContractError("workflow.plan.action", "plan action is not target-owned")
    material = MaterialContextSeed(
        workflow_definition_sha256=plan.gate_context.workflow_definition_sha256,
        policy_bundle_sha256=plan.gate_context.policy_bundle_sha256,
        rules_bundle_sha256=plan.gate_context.rules_bundle_sha256,
        effective_config_sha256=plan.gate_context.effective_config_sha256,
        current_manifest_sha256=plan.gate_context.current_manifest_sha256,
        evidence_graph_sha256=plan.gate_context.evidence_graph_sha256,
    )
    expected = canonical_sha256(
        _plan_identity(
            definition,
            material,
            action,
            tuple(str(value) for value in plan.consumed_evidence_sha256s),
        )
    )
    if str(expected) != str(plan.plan_sha256):
        raise WorkflowContractError("workflow.plan.digest", "executable plan digest mismatch")
    if str(plan.gate_context.executable_plan_sha256) != str(plan.plan_sha256):
        raise WorkflowContractError("workflow.plan.context", "GateContext is bound to another plan")
    if str(plan.plan_id) != f"production-plan-{str(expected)[:20]}":
        raise WorkflowContractError("workflow.plan.identity", "executable plan id mismatch")
    if (
        plan.capability_id != action.capability_id
        or plan.actor_role != action.actor_role
        or plan.risk is not action.risk
        or plan.authority_requirement is not action.authority_requirement
        or plan.side_effect is not action.side_effect
        or plan.consumed_claim_ids != action.required_claim_ids
        or plan.prohibited_actions != action.prohibited_actions
    ):
        raise WorkflowContractError("workflow.plan.rebound", "plan fields were rebound from the target action")
    return plan
