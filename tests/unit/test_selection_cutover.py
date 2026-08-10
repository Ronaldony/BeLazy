from __future__ import annotations

import pytest

from video_factory.quality import target_quality_policy
from video_factory.selection import (
    CandidateDecisionVerificationInputs,
    CandidateSelectionCutoverMode,
    SelectionContractError,
    route_candidate_selection,
)
from video_factory.workflow import default_workflow_definition
from tests.unit.test_authority_control import NOW
from tests.unit.test_candidate_decision import (
    _CurrentConfidenceVerifier,
    _decision,
)
from tests.unit.test_quality_bundle import (
    _CurrentEvaluationVerifier,
    _CurrentMediaResolver,
)


def _verification(value, bundle_ref, bundle, candidates, evidence, ledger):
    return CandidateDecisionVerificationInputs(
        workspace_id=str(value.workspace_id),
        channel_id=str(value.channel_id),
        concept_id=str(value.concept_id),
        episode_id=str(value.episode_id),
        quality_origin_evaluated_at=bundle.evaluated_at,
        quality_bundle_ref=bundle_ref,
        quality_bundle=bundle,
        candidate_sets=(candidates,),
        policy=target_quality_policy(),
        current_context=value.gate_context,
        verified_at=NOW,
        origin_evaluated_at=NOW,
        origin_authority=evidence,
        origin_authority_ledger=ledger,
        authority=evidence,
        authority_ledger=ledger,
        quality_resolver=_CurrentMediaResolver(),
        evaluation_verifier=_CurrentEvaluationVerifier(),
        confidence_verifier=_CurrentConfidenceVerifier(),
    )


def test_verified_auto_selection_bypasses_only_the_legacy_human_selection_step():
    value, bundle_ref, bundle, candidates, evidence, ledger = _decision()

    route = route_candidate_selection(
        value,
        mode=CandidateSelectionCutoverMode.VERIFIED_AUTOMATION,
        verification=_verification(
            value, bundle_ref, bundle, candidates, evidence, ledger
        ),
    )

    assert str(route.action_id) == "assemble_selected_candidates"
    assert str(route.actor_role) == "automation-coordinator"
    assert route.selected_subjects == (value.shots[0].selected_subject,)
    assert route.human_selection_required is False
    assert route.authority_effect == "none"
    assert "select_edit_inputs" in {
        str(action.action_id) for action in default_workflow_definition().actions
    }


def test_low_confidence_verified_decision_routes_to_bounded_human_exception():
    value, bundle_ref, bundle, candidates, evidence, ledger = _decision(
        confidence=(8499, 9000)
    )

    route = route_candidate_selection(
        value,
        mode=CandidateSelectionCutoverMode.VERIFIED_AUTOMATION,
        verification=_verification(
            value, bundle_ref, bundle, candidates, evidence, ledger
        ),
    )

    assert str(route.action_id) == "select_edit_inputs"
    assert route.human_selection_required is True
    assert route.selected_subjects == ()
    assert "selection.cutover.escalation_required" in route.reason_codes


def test_cutover_rollback_restores_legacy_human_route_without_relabeling():
    value, bundle_ref, bundle, candidates, evidence, ledger = _decision()

    route = route_candidate_selection(
        value,
        mode=CandidateSelectionCutoverMode.LEGACY_HUMAN_FALLBACK,
    )

    assert str(route.action_id) == "select_edit_inputs"
    assert str(route.actor_role) == "human-editor"
    assert route.human_selection_required is True
    assert route.selected_subjects == ()
    assert route.authority_effect == "none"


def test_verified_automation_never_accepts_an_unverified_decision():
    value, bundle_ref, bundle, candidates, evidence, ledger = _decision()

    with pytest.raises(SelectionContractError) as caught:
        route_candidate_selection(
            value,
            mode=CandidateSelectionCutoverMode.VERIFIED_AUTOMATION,
        )

    assert caught.value.reason_code == "selection.cutover.verification_missing"
