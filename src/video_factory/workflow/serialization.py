"""Strict JSON boundaries for W04 workflow artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeAlias, cast

from video_factory.approvals import gate_context_from_mapping
from video_factory.artifacts import validate_artifact_mapping
from video_factory.config import canonical_json_bytes
from video_factory.domain import HashDigest, OpaqueId
from video_factory.json_boundary import (
    JsonInputError,
    parse_json_bytes,
    require_json_object,
    validate_json_mapping,
)

from .contracts import (
    ActionBlocker,
    ActionDefinition,
    ActionFrontierItem,
    ActionRisk,
    AuthorityRequirement,
    ClaimDefinition,
    ExecutableProductionPlan,
    GateDefinition,
    GateResult,
    GateStatus,
    MaterialContextSeed,
    ParityDifference,
    WorkflowContractError,
    WorkflowDefinition,
    WorkflowEvaluation,
    WorkflowParityReport,
)
from .definition import validate_workflow_definition, workflow_definition_to_mapping
from .evaluator import (
    executable_plan_to_mapping,
    gate_result_to_mapping,
    validate_executable_production_plan_structure,
    validate_gate_result,
    validate_workflow_evaluation,
    workflow_evaluation_to_mapping,
)
from .definition import default_workflow_definition
from .parity import validate_workflow_parity_report, workflow_parity_report_to_mapping


WorkflowArtifact: TypeAlias = (
    WorkflowDefinition
    | GateResult
    | WorkflowEvaluation
    | ExecutableProductionPlan
    | WorkflowParityReport
)


def _schema(document: Mapping[str, object], version: str) -> dict[str, object]:
    try:
        value = dict(validate_json_mapping(document))
    except JsonInputError as error:
        raise WorkflowContractError(error.code.value, error.detail) from error
    if value.get("artifact_version") != version:
        raise WorkflowContractError("workflow.serialization.version", f"expected {version}")
    report = validate_artifact_mapping(value)
    if not report.ok:
        first = report.errors[0]
        raise WorkflowContractError(
            first.reason_code or "workflow.serialization.schema", first.as_text()
        )
    return value


def _exact(value: Mapping[str, object], rendered: Mapping[str, object]) -> None:
    if dict(value) != dict(rendered):
        raise WorkflowContractError("workflow.serialization.canonical", "artifact mapping is not canonical")


def _gate(value: Mapping[str, object]) -> GateResult:
    result = GateResult(
        artifact_version=str(value["artifact_version"]),
        gate_id=OpaqueId(str(value["gate_id"])),
        consumed_context_sha256=HashDigest(
            str(value["consumed_context_sha256"])
        ),
        status=GateStatus(str(value["status"])),
        reason_codes=tuple(cast(list[str], value["reason_codes"])),
        messages=tuple(cast(list[str], value["messages"])),
        evidence_sha256s=tuple(HashDigest(str(item)) for item in cast(list[str], value["evidence_sha256s"])),
        result_sha256=HashDigest(str(value["result_sha256"])),
    )
    return validate_gate_result(result)


def workflow_definition_from_mapping(document: Mapping[str, object]) -> WorkflowDefinition:
    value = _schema(document, "workflow-definition/1.0")
    claims = tuple(
        ClaimDefinition(
            claim_id=OpaqueId(str(item["claim_id"])),
            dependency_claim_ids=tuple(OpaqueId(str(key)) for key in item["dependency_claim_ids"]),
            gate_id=OpaqueId(str(item["gate_id"])),
        )
        for item in cast(list[dict[str, object]], value["claims"])
    )
    gates = tuple(
        GateDefinition(
            gate_id=OpaqueId(str(item["gate_id"])),
            owner=str(item["owner"]),
            stable_reason_prefix=str(item["stable_reason_prefix"]),
            consumed_context_fields=tuple(cast(list[str], item["consumed_context_fields"])),
        )
        for item in cast(list[dict[str, object]], value["gates"])
    )
    actions = tuple(
        ActionDefinition(
            action_id=OpaqueId(str(item["action_id"])),
            satisfies_claim_id=OpaqueId(str(item["satisfies_claim_id"])),
            required_claim_ids=tuple(OpaqueId(str(key)) for key in item["required_claim_ids"]),
            actor_role=str(item["actor_role"]),
            capability_id=OpaqueId(str(item["capability_id"])),
            risk=ActionRisk(str(item["risk"])),
            authority_requirement=AuthorityRequirement(str(item["authority_requirement"])),
            priority=int(item["priority"]),
            side_effect=bool(item["side_effect"]),
            trigger_reason_codes=tuple(cast(list[str], item["trigger_reason_codes"])),
            prohibited_actions=tuple(cast(list[str], item["prohibited_actions"])),
        )
        for item in cast(list[dict[str, object]], value["actions"])
    )
    result = WorkflowDefinition(
        artifact_version=str(value["artifact_version"]),
        workflow_id=OpaqueId(str(value["workflow_id"])),
        workflow_version=str(value["workflow_version"]),
        definition_sha256=HashDigest(str(value["definition_sha256"])),
        claims=claims,
        gates=gates,
        actions=actions,
    )
    validate_workflow_definition(result)
    _exact(value, workflow_definition_to_mapping(result))
    return result


def gate_result_from_mapping(document: Mapping[str, object]) -> GateResult:
    value = _schema(document, "gate-result/1.0")
    result = _gate(value)
    _exact(value, gate_result_to_mapping(result))
    return result


def _blocker(value: Mapping[str, object]) -> ActionBlocker:
    return ActionBlocker(
        claim_id=OpaqueId(str(value["claim_id"])),
        gate_id=OpaqueId(str(value["gate_id"])),
        reason_code=str(value["reason_code"]),
        message=str(value["message"]),
    )


def workflow_evaluation_from_mapping(document: Mapping[str, object]) -> WorkflowEvaluation:
    value = _schema(document, "workflow-evaluation/1.0")
    context = cast(dict[str, object], value["material_context"])
    frontier = tuple(
        ActionFrontierItem(
            action_id=OpaqueId(str(item["action_id"])),
            plan_sha256=HashDigest(str(item["plan_sha256"])),
            actor_role=str(item["actor_role"]),
            capability_id=OpaqueId(str(item["capability_id"])),
            risk=ActionRisk(str(item["risk"])),
            authority_requirement=AuthorityRequirement(str(item["authority_requirement"])),
            consumed_claim_ids=tuple(OpaqueId(str(key)) for key in item["consumed_claim_ids"]),
            consumed_evidence_sha256s=tuple(HashDigest(str(key)) for key in item["consumed_evidence_sha256s"]),
            blockers=tuple(_blocker(key) for key in item["blockers"]),
            prohibited_actions=tuple(cast(list[str], item["prohibited_actions"])),
            authority_effect=str(item["authority_effect"]),
        )
        for item in cast(list[dict[str, object]], value["action_frontier"])
    )
    recommended = value["recommended_action_id"]
    result = WorkflowEvaluation(
        artifact_version=str(value["artifact_version"]),
        evaluation_id=OpaqueId(str(value["evaluation_id"])),
        evaluation_sha256=HashDigest(str(value["evaluation_sha256"])),
        workflow_id=OpaqueId(str(value["workflow_id"])),
        workflow_definition_sha256=HashDigest(str(value["workflow_definition_sha256"])),
        material_context=MaterialContextSeed(
            workflow_definition_sha256=HashDigest(str(context["workflow_definition_sha256"])),
            policy_bundle_sha256=HashDigest(str(context["policy_bundle_sha256"])),
            rules_bundle_sha256=HashDigest(str(context["rules_bundle_sha256"])),
            effective_config_sha256=HashDigest(str(context["effective_config_sha256"])),
            current_manifest_sha256=HashDigest(str(context["current_manifest_sha256"])),
            evidence_graph_sha256=HashDigest(str(context["evidence_graph_sha256"])),
        ),
        material_context_sha256=HashDigest(str(value["material_context_sha256"])),
        gate_results=tuple(_gate(item) for item in cast(list[dict[str, object]], value["gate_results"])),
        gate_input_sha256s=tuple(
            (OpaqueId(str(item["gate_id"])), HashDigest(str(item["sha256"])))
            for item in cast(list[dict[str, object]], value["gate_input_sha256s"])
        ),
        satisfied_claim_ids=tuple(OpaqueId(str(item)) for item in cast(list[str], value["satisfied_claim_ids"])),
        blockers=tuple(_blocker(item) for item in cast(list[dict[str, object]], value["blockers"])),
        action_frontier=frontier,
        recommended_action_id=OpaqueId(str(recommended)) if recommended is not None else None,
        previous_evaluation_sha256=(
            HashDigest(str(value["previous_evaluation_sha256"]))
            if value["previous_evaluation_sha256"] is not None
            else None
        ),
        invalidated_claim_ids=tuple(OpaqueId(str(item)) for item in cast(list[str], value["invalidated_claim_ids"])),
        reused_claim_ids=tuple(OpaqueId(str(item)) for item in cast(list[str], value["reused_claim_ids"])),
        authority_effect=str(value["authority_effect"]),
    )
    validate_workflow_evaluation(result)
    _exact(value, workflow_evaluation_to_mapping(result))
    return result


def executable_production_plan_from_mapping(document: Mapping[str, object]) -> ExecutableProductionPlan:
    value = _schema(document, "executable-production-plan/1.0")
    result = ExecutableProductionPlan(
        artifact_version=str(value["artifact_version"]),
        plan_id=OpaqueId(str(value["plan_id"])),
        plan_sha256=HashDigest(str(value["plan_sha256"])),
        workflow_evaluation_sha256=HashDigest(str(value["workflow_evaluation_sha256"])),
        workflow_definition_sha256=HashDigest(str(value["workflow_definition_sha256"])),
        action_id=OpaqueId(str(value["action_id"])),
        capability_id=OpaqueId(str(value["capability_id"])),
        actor_role=str(value["actor_role"]),
        risk=ActionRisk(str(value["risk"])),
        authority_requirement=AuthorityRequirement(str(value["authority_requirement"])),
        side_effect=bool(value["side_effect"]),
        gate_context=gate_context_from_mapping(cast(Mapping[str, object], value["gate_context"])),
        consumed_claim_ids=tuple(OpaqueId(str(item)) for item in cast(list[str], value["consumed_claim_ids"])),
        consumed_evidence_sha256s=tuple(HashDigest(str(item)) for item in cast(list[str], value["consumed_evidence_sha256s"])),
        prohibited_actions=tuple(cast(list[str], value["prohibited_actions"])),
        authority_effect=str(value["authority_effect"]),
    )
    validate_executable_production_plan_structure(
        default_workflow_definition(), result
    )
    _exact(value, executable_plan_to_mapping(result))
    return result


def workflow_parity_report_from_mapping(document: Mapping[str, object]) -> WorkflowParityReport:
    value = _schema(document, "workflow-parity-report/1.0")
    result = WorkflowParityReport(
        artifact_version=str(value["artifact_version"]),
        report_id=OpaqueId(str(value["report_id"])),
        report_sha256=HashDigest(str(value["report_sha256"])),
        normalization_version=str(value["normalization_version"]),
        normalization_sha256=HashDigest(str(value["normalization_sha256"])),
        legacy_projection_sha256=HashDigest(str(value["legacy_projection_sha256"])),
        workflow_evaluation_sha256=HashDigest(str(value["workflow_evaluation_sha256"])),
        differences=tuple(
            ParityDifference(
                dimension=str(item["dimension"]),
                legacy_value_sha256=HashDigest(str(item["legacy_value_sha256"])),
                declarative_value_sha256=HashDigest(str(item["declarative_value_sha256"])),
                explanation_code=(str(item["explanation_code"]) if item["explanation_code"] is not None else None),
            )
            for item in cast(list[dict[str, object]], value["differences"])
        ),
        unexplained_dimensions=tuple(cast(list[str], value["unexplained_dimensions"])),
        parity_pass=bool(value["parity_pass"]),
        cutover_applied=bool(value["cutover_applied"]),
        authority_effect=str(value["authority_effect"]),
    )
    validate_workflow_parity_report(result)
    _exact(value, workflow_parity_report_to_mapping(result))
    return result


def workflow_artifact_from_mapping(document: Mapping[str, object]) -> WorkflowArtifact:
    version = document.get("artifact_version")
    loaders = {
        "workflow-definition/1.0": workflow_definition_from_mapping,
        "gate-result/1.0": gate_result_from_mapping,
        "workflow-evaluation/1.0": workflow_evaluation_from_mapping,
        "executable-production-plan/1.0": executable_production_plan_from_mapping,
        "workflow-parity-report/1.0": workflow_parity_report_from_mapping,
    }
    loader = loaders.get(version)
    if loader is None:
        raise WorkflowContractError("workflow.serialization.version", "unsupported workflow artifact")
    return loader(document)


def workflow_artifact_from_bytes(payload: bytes) -> WorkflowArtifact:
    return workflow_artifact_from_mapping(require_json_object(parse_json_bytes(payload)))


def workflow_artifact_to_mapping(artifact: WorkflowArtifact) -> dict[str, object]:
    if isinstance(artifact, WorkflowDefinition):
        return workflow_definition_to_mapping(artifact)
    if isinstance(artifact, GateResult):
        return gate_result_to_mapping(artifact)
    if isinstance(artifact, WorkflowEvaluation):
        return workflow_evaluation_to_mapping(artifact)
    if isinstance(artifact, ExecutableProductionPlan):
        return executable_plan_to_mapping(artifact)
    if isinstance(artifact, WorkflowParityReport):
        return workflow_parity_report_to_mapping(artifact)
    raise WorkflowContractError("workflow.serialization.type", "unsupported workflow artifact")


def workflow_artifact_to_bytes(artifact: WorkflowArtifact) -> bytes:
    return canonical_json_bytes(workflow_artifact_to_mapping(artifact))
