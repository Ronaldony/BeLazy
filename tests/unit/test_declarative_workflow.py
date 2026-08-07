"""W04 declarative DAG, invalidation and legacy-parity contracts."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from importlib.resources import files

import pytest

from video_factory.artifacts import validate_artifact_mapping
from video_factory.authority import target_policy_bundle
from video_factory.config import canonical_sha256
from video_factory.domain import HashDigest, OpaqueId
from video_factory.workflow import (
    GateStatus,
    LegacyNextStepProjection,
    MaterialContextSeed,
    WorkflowContractError,
    build_executable_production_plan,
    build_gate_result,
    compare_legacy_parity,
    default_workflow_definition,
    evaluate_workflow,
    executable_plan_to_mapping,
    gate_result_to_mapping,
    require_target_workflow_definition,
    require_target_workflow_evaluation,
    validate_workflow_parity_report,
    validate_workflow_evaluation,
    validate_workflow_definition,
    workflow_artifact_from_bytes,
    workflow_artifact_to_bytes,
    workflow_definition_to_mapping,
    workflow_definition_sha256,
    workflow_evaluation_to_mapping,
    workflow_parity_report_to_mapping,
    workflow_semantic_projection,
)
from video_factory.workflow.resources import (
    RESOURCE_FILENAMES,
    RESOURCE_PACKAGE,
    validate_packaged_workflow_resources,
    workflow_resource_bytes,
    workflow_resource_manifest,
)


MODES = ("rapid", "standard", "controlled")


def _context(mode: str = "standard") -> MaterialContextSeed:
    definition = default_workflow_definition()
    policy = target_policy_bundle()
    marker = {"rapid": "1", "standard": "2", "controlled": "3"}[mode]
    return MaterialContextSeed(
        workflow_definition_sha256=definition.definition_sha256,
        policy_bundle_sha256=policy.bundle_sha256,
        rules_bundle_sha256=HashDigest("4" * 64),
        effective_config_sha256=HashDigest(marker * 64),
        current_manifest_sha256=HashDigest("5" * 64),
        evidence_graph_sha256=HashDigest("6" * 64),
    )


def _result(gate_id: str, passed: bool, reason: str | None = None):
    return build_gate_result(
        gate_id,
        GateStatus.PASS if passed else GateStatus.BLOCKED,
        reason_codes=() if passed else (reason or "workflow.test.blocked",),
        messages=() if passed else ("blocked",),
        evidence_sha256s=("a" * 64,),
    )


def _evaluation_for_action(action_id: str, mode: str = "standard"):
    definition = default_workflow_definition()
    action = next(item for item in definition.actions if str(item.action_id) == action_id)
    results = {}
    # Pass every earlier target so independent parallel work cannot hide the
    # requested characterization branch.  Same-claim alternatives are selected
    # solely by their stable trigger reason.
    earlier_claims = {
        str(item.satisfies_claim_id)
        for item in definition.actions
        if item.priority < action.priority
        and item.satisfies_claim_id != action.satisfies_claim_id
    }
    for claim in definition.claims:
        claim_id = str(claim.claim_id)
        if claim_id in earlier_claims:
            results[str(claim.gate_id)] = _result(str(claim.gate_id), True)
        else:
            results[str(claim.gate_id)] = build_gate_result(
                str(claim.gate_id),
                GateStatus.UNKNOWN,
                reason_codes=("workflow.gate.missing",),
                messages=("not observed",),
            )
    reason = (
        action.trigger_reason_codes[0]
        if action.trigger_reason_codes
        else f"workflow.{action.satisfies_claim_id}.blocked"
    )
    target_gate = next(
        claim.gate_id
        for claim in definition.claims
        if claim.claim_id == action.satisfies_claim_id
    )
    results[str(target_gate)] = _result(str(target_gate), False, reason)
    evaluation = evaluate_workflow(
        definition,
        tuple(results[str(gate.gate_id)] for gate in definition.gates),
        _context(mode),
    )
    return definition, evaluation


def test_target_definition_is_closed_versioned_and_schema_valid() -> None:
    definition = default_workflow_definition()
    assert len(definition.claims) == 24
    assert len(definition.gates) == 24
    assert len(definition.actions) == 26
    assert require_target_workflow_definition(definition) is definition
    mapping = workflow_definition_to_mapping(definition)
    assert validate_artifact_mapping(mapping).ok
    assert workflow_artifact_from_bytes(workflow_artifact_to_bytes(definition)) == definition

    reduced = replace(definition, actions=definition.actions[:-1])
    with pytest.raises(WorkflowContractError, match="digest"):
        validate_workflow_definition(reduced)
    provisional = replace(
        reduced,
        workflow_id="pending",
        definition_sha256=HashDigest("0" * 64),
    )
    digest = workflow_definition_sha256(provisional)
    custom = replace(
        provisional,
        workflow_id=f"workflow-{str(digest)[:20]}",
        definition_sha256=digest,
    )
    assert validate_workflow_definition(custom) is custom
    with pytest.raises(WorkflowContractError, match="target-owned"):
        require_target_workflow_definition(custom)


def test_packaged_workflow_authority_resources_are_exact_code_projections() -> None:
    resource_root = files(RESOURCE_PACKAGE)
    expected = workflow_resource_bytes()
    assert tuple(expected) == RESOURCE_FILENAMES
    for filename, payload in expected.items():
        assert resource_root.joinpath(filename).read_bytes() == payload
    manifest_payload = resource_root.joinpath(
        "workflow-authority-resource-manifest.json"
    ).read_bytes()
    assert validate_packaged_workflow_resources() == hashlib.sha256(
        manifest_payload
    ).hexdigest()
    assert workflow_resource_manifest()["resource_count"] == 3


def test_definition_rejects_cycle_duplicate_and_unknown_gate() -> None:
    definition = default_workflow_definition()
    first = definition.claims[0]
    cycled = replace(first, dependency_claim_ids=(definition.claims[-1].claim_id,))
    with pytest.raises(WorkflowContractError, match="cycle"):
        validate_workflow_definition(replace(definition, claims=(cycled, *definition.claims[1:])))
    with pytest.raises(WorkflowContractError, match="duplicate"):
        validate_workflow_definition(replace(definition, claims=(*definition.claims, first)))
    with pytest.raises(WorkflowContractError, match="unknown gate"):
        validate_workflow_definition(
            replace(definition, claims=(replace(first, gate_id="unknown"), *definition.claims[1:]))
        )


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "action_id", [str(item.action_id) for item in default_workflow_definition().actions]
)
def test_all_26_declarative_actions_are_stable_across_three_context_profiles(
    action_id: str,
    mode: str,
) -> None:
    definition, evaluation = _evaluation_for_action(action_id, mode)
    assert str(evaluation.recommended_action_id) == action_id
    assert evaluation.authority_effect == "none"
    assert all(item.authority_effect == "none" for item in evaluation.action_frontier)
    assert validate_artifact_mapping(workflow_evaluation_to_mapping(evaluation)).ok
    assert workflow_artifact_from_bytes(workflow_artifact_to_bytes(evaluation)) == evaluation
    plan = build_executable_production_plan(definition, evaluation, action_id)
    assert plan.gate_context.executable_plan_sha256 == plan.plan_sha256
    assert plan.authority_effect == "none"
    assert validate_artifact_mapping(executable_plan_to_mapping(plan)).ok


def test_packet_review_and_feasibility_are_exposed_as_parallel_frontier() -> None:
    definition = default_workflow_definition()
    pass_through = {
        "artifact_graph_valid", "brief_present", "storyboard_present",
        "storyboard_review_pass", "storyboard_approval_current", "packet_present",
        "packet_contract_valid",
    }
    results = []
    for claim in definition.claims:
        if str(claim.claim_id) in pass_through:
            results.append(_result(str(claim.gate_id), True))
        elif str(claim.claim_id) in {"packet_review_pass", "feasibility_pass"}:
            results.append(_result(str(claim.gate_id), False, f"workflow.{claim.claim_id}.blocked"))
        else:
            results.append(
                build_gate_result(
                    str(claim.gate_id), GateStatus.UNKNOWN,
                    reason_codes=("workflow.gate.missing",), messages=("missing",),
                )
            )
    evaluation = evaluate_workflow(definition, results, _context())
    assert [str(item.action_id) for item in evaluation.action_frontier] == [
        "review_or_revise_generation_packet",
        "review_generation_feasibility",
    ]
    first = build_executable_production_plan(
        definition, evaluation, "review_or_revise_generation_packet"
    )
    second = build_executable_production_plan(
        definition, evaluation, "review_generation_feasibility"
    )
    assert first.gate_context != second.gate_context
    assert first.plan_sha256 != second.plan_sha256


def test_incremental_invalidation_matches_clean_semantics_and_reuses_upstream() -> None:
    definition, baseline = _evaluation_for_action("review_generation_feasibility")
    results = list(baseline.gate_results)
    target = next(
        index for index, item in enumerate(results)
        if str(item.gate_id).endswith("packet_review_pass")
    )
    results[target] = _result(str(results[target].gate_id), False, "workflow.packet_review_pass.changed")
    incremental = evaluate_workflow(definition, results, _context(), previous=baseline)
    clean = evaluate_workflow(definition, results, _context())
    assert workflow_semantic_projection(incremental) == workflow_semantic_projection(clean)
    assert "artifact_graph_valid" in incremental.reused_claim_ids
    assert "packet_review_pass" in incremental.invalidated_claim_ids
    assert "generation_approval_current" in incremental.invalidated_claim_ids

    context_changed = evaluate_workflow(
        definition, results,
        replace(_context(), current_manifest_sha256=HashDigest("f" * 64)),
        previous=incremental,
    )
    assert len(context_changed.invalidated_claim_ids) == len(definition.claims)


def test_self_rehashed_frontier_rebound_cannot_feed_a_production_plan() -> None:
    definition, early = _evaluation_for_action("create_brief")
    _, late = _evaluation_for_action("ready_for_human_publish")
    mapping = workflow_evaluation_to_mapping(early)
    late_mapping = workflow_evaluation_to_mapping(late)
    mapping["action_frontier"] = late_mapping["action_frontier"]
    mapping["recommended_action_id"] = late_mapping["recommended_action_id"]
    identity = {
        key: value
        for key, value in mapping.items()
        if key not in {"evaluation_id", "evaluation_sha256"}
    }
    digest = canonical_sha256(identity)
    forged = replace(
        early,
        evaluation_id=OpaqueId(f"workflow-evaluation-{str(digest)[:20]}"),
        evaluation_sha256=digest,
        action_frontier=late.action_frontier,
        recommended_action_id=late.recommended_action_id,
    )
    assert validate_workflow_evaluation(forged) is forged
    with pytest.raises(WorkflowContractError, match="clean target-DAG"):
        require_target_workflow_evaluation(forged)
    with pytest.raises(WorkflowContractError, match="clean target-DAG"):
        build_executable_production_plan(
            definition,
            forged,
            "ready_for_human_publish",
        )


def test_gate_result_is_strict_and_schema_registered() -> None:
    result = _result("gate.example", True)
    assert validate_artifact_mapping(gate_result_to_mapping(result)).ok
    with pytest.raises(WorkflowContractError, match="passing gate"):
        build_gate_result(
            "gate.example", GateStatus.PASS,
            reason_codes=("workflow.invalid",), messages=("bad",),
        )


def test_parity_report_never_cuts_over_or_grants_authority() -> None:
    _, evaluation = _evaluation_for_action("create_brief")
    item = evaluation.action_frontier[0]
    legacy = LegacyNextStepProjection(
        action_type="create_brief",
        actor_role="creator",
        approval_required=False,
        blockers=("brief missing",),
        consumed_evidence=(),
        prohibited_actions=item.prohibited_actions,
        authority_effect="none",
    )
    report = compare_legacy_parity(
        legacy,
        evaluation,
        explained_dimensions={
            "blockers": "EXPLAINED_AUTHORITY_HARDENING",
            "consumed_evidence": "EXPLAINED_AUTHORITY_HARDENING",
        },
    )
    assert report.parity_pass
    assert report.cutover_applied is False
    assert report.authority_effect == "none"
    assert validate_artifact_mapping(workflow_parity_report_to_mapping(report)).ok

    unmapped = replace(legacy, blockers=("totally opaque legacy text",))
    failed = compare_legacy_parity(unmapped, evaluation)
    assert failed.parity_pass is False
    assert "blockers" in failed.unexplained_dimensions

    with pytest.raises(WorkflowContractError, match="contract"):
        validate_workflow_parity_report(
            replace(failed, unexplained_dimensions=(), parity_pass=True)
        )
    with pytest.raises(WorkflowContractError, match="contract"):
        validate_workflow_parity_report(
            replace(failed, normalization_sha256=HashDigest("f" * 64))
        )
