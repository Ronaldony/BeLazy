from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from video_factory.authority import (
    AuthorityDecisionStatus,
    ActionRisk,
    AuthoritySource,
    build_action_authority_request,
    LedgerRecordState,
    build_bound_action_authority_request,
    VerificationPurpose,
    evaluate_authority,
    target_policy_bundle,
)
from video_factory.domain import ArtifactReference, ArtifactVersion, HashDigest, OpaqueId, RelativeArtifactPath
from video_factory.quality import (
    InitialAuthorityEvidence,
    MediaSubject,
    QualityDimension,
    QualityVerdict,
    build_dimension_evaluation,
    build_quality_bundle,
    quality_bundle_bytes_sha256,
    target_quality_policy,
)
from video_factory.selection import (
    AUTO_SELECT_CANDIDATES_ACTION_ID,
    AUTO_SELECT_CANDIDATES_CAPABILITY_ID,
    CANDIDATE_CONFIDENCE_RECEIPT_VERSION,
    CandidateDecisionStatus,
    CandidateDecisionVerificationInputs,
    CandidateOption,
    SelectionContractError,
    ShotCandidateSet,
    build_candidate_decision,
    candidate_selection_input_sha256,
    verify_candidate_decision,
)
from video_factory.workflow import AuthorityRequirement
from tests.unit.test_authority_control import FakeLedger, NOW, _request
from tests.unit.test_quality_bundle import (
    _CurrentEvaluationVerifier,
    _CurrentMediaResolver,
)


def _ref(path: str, digest: str, version: str) -> ArtifactReference:
    return ArtifactReference(
        RelativeArtifactPath(path),
        HashDigest((digest * 64)[:64]),
        ArtifactVersion(version),
    )


def _subject(name: str, digest: str) -> MediaSubject:
    return MediaSubject(
        shot_id=OpaqueId("shot-a"),
        component_id=OpaqueId(f"candidate-{name}"),
        reference=_ref(f"media/{name}.mp4", digest, "generated-media/1.0"),
        byte_length=1000,
        workspace_observation_sha256=HashDigest("d" * 64),
        observation_receipt_ref=_ref(
            f"evidence/{name}-observation.json",
            digest,
            "quality-observation-receipt/1.0",
        ),
    )


def _bundle(context, scores=(9000, 8200), *, hard_fail=False):
    policy = target_quality_policy()
    subjects = (_subject("a", "a"), _subject("b", "b"))
    evaluations = []
    for subject_index, (subject, score) in enumerate(zip(subjects, scores, strict=True)):
        for dimension_index, dimension in enumerate(QualityDimension):
            failed = hard_fail and subject_index == 0 and dimension is QualityDimension.CONTINUITY
            evaluations.append(
                build_dimension_evaluation(
                    dimension=dimension,
                    subject=subject,
                    evaluator_id=f"evaluator-{subject_index}-{dimension_index}",
                    evaluator_version="1.0",
                    evaluator_receipt_ref=_ref(
                        f"evidence/{subject_index}-{dimension.value}.json",
                        format(subject_index * 9 + dimension_index + 1, "x"),
                        "quality-evaluation-receipt/1.0",
                    ),
                    gate_context=context,
                    policy=policy,
                    verdict=QualityVerdict.FAIL if failed else QualityVerdict.PASS,
                    score_bps=score,
                    hard_failure=failed,
                    reason_codes=("quality.continuity.failed",) if failed else (),
                )
            )
    return subjects, build_quality_bundle(
        episode_id="episode-a",
        gate_context=context,
        evaluations=evaluations,
        policy=policy,
        evaluated_at=NOW.isoformat(),
    )


class _CurrentConfidenceVerifier:
    def __init__(self, *, stale_call: int | None = None) -> None:
        self.calls = 0
        self.stale_call = stale_call

    def verify_current(self, option, *, gate_context, policy, evaluated_at):
        self.calls += 1
        if self.calls == self.stale_call:
            return replace(option, confidence_bps=option.confidence_bps - 1)
        return option


def _authority(bundle_ref, candidate_set, selection_input_sha256, context):
    base = _request("rank_generation_candidates")
    inputs = tuple(
        sorted(
            (
                bundle_ref,
                *(item.subject.reference for item in candidate_set.candidates),
                *(item.confidence_receipt_ref for item in candidate_set.candidates),
            ),
            key=lambda item: (
                str(item.path).casefold(),
                str(item.path),
                str(item.sha256),
                str(item.artifact_version),
            ),
        )
    )
    request = build_bound_action_authority_request(
        request_id="candidate-selection-request-a",
        request_envelope_sha256=str(selection_input_sha256),
        idempotency_key="candidate-selection-a",
        requester_principal_id="requester-a",
        action_id=AUTO_SELECT_CANDIDATES_ACTION_ID,
        capability_id=AUTO_SELECT_CANDIDATES_CAPABILITY_ID,
        executable_plan_sha256=str(context.executable_plan_sha256),
        action_risk=ActionRisk.R1,
        authority_requirement=AuthorityRequirement.POLICY,
        side_effect=False,
        gate_context=context,
        profiles=base.profiles,
        scope=replace(
            base.scope,
            provider_id=None,
            model_id=None,
            destination=None,
            cost_minor_units=0,
            candidate_count=len(candidate_set.candidates),
            retry_index=0,
            input_artifacts=inputs,
            allowed_outputs=(),
        ),
        hard_escalation_facts=base.hard_escalation_facts,
    )
    ledger = FakeLedger(AuthoritySource.POLICY, ())
    risk, decision = evaluate_authority(
        request,
        target_policy_bundle(),
        ledger=ledger,
        evaluated_at=NOW,
    )
    assert decision.status is AuthorityDecisionStatus.AUTHORIZED
    receipt = ledger._receipt(
        request,
        risk.assessment_sha256,
        purpose=VerificationPurpose.INITIAL_DECISION,
        evaluated_at=NOW,
    )
    assert receipt.receipt_sha256 == decision.verification_receipt_sha256
    return InitialAuthorityEvidence(request, risk, decision, receipt), ledger


def _decision(
    scores=(9000, 8200),
    confidence=(9000, 9000),
    *,
    authority=True,
    hard_fail=False,
    confidence_verifier=None,
):
    base = _request("rank_generation_candidates")
    subjects, bundle = _bundle(base.gate_context, scores, hard_fail=hard_fail)
    bundle_ref = _ref("quality/bundle.json", "0", "quality-bundle/1.0")
    bundle_ref = replace(bundle_ref, sha256=quality_bundle_bytes_sha256(bundle))
    candidate_set = ShotCandidateSet(
        OpaqueId("shot-a"),
        tuple(
            CandidateOption(
                subject,
                OpaqueId(f"adapter-{index}"),
                confidence[index],
                _ref(
                    f"evidence/confidence-{index}.json",
                    format(index + 14, "x"),
                    CANDIDATE_CONFIDENCE_RECEIPT_VERSION,
                ),
            )
            for index, subject in enumerate(subjects)
        ),
    )
    current_confidence = confidence_verifier or _CurrentConfidenceVerifier()
    selection_input = candidate_selection_input_sha256(
        episode_id="episode-a",
        quality_bundle_ref=bundle_ref,
        quality_bundle=bundle,
        candidate_sets=(candidate_set,),
        policy=target_quality_policy(),
        current_context=base.gate_context,
    )
    evidence, ledger = (
        _authority(
            bundle_ref,
            candidate_set,
            selection_input,
            base.gate_context,
        )
        if authority
        else (None, None)
    )
    value = build_candidate_decision(
        episode_id="episode-a",
        quality_bundle_ref=bundle_ref,
        quality_bundle=bundle,
        candidate_sets=(candidate_set,),
        policy=target_quality_policy(),
        current_context=base.gate_context,
        evaluated_at=NOW,
        authority=evidence,
        authority_ledger=ledger,
        quality_resolver=_CurrentMediaResolver(),
        evaluation_verifier=_CurrentEvaluationVerifier(),
        confidence_verifier=current_confidence,
    )
    return value, bundle_ref, bundle, candidate_set, evidence, ledger


def test_candidate_auto_selects_only_at_all_thresholds_with_current_authority() -> None:
    value, bundle_ref, bundle, candidates, evidence, ledger = _decision()
    assert value.status is CandidateDecisionStatus.AUTO_SELECTED
    assert value.shots[0].selected_subject == candidates.candidates[0].subject
    assert value.shots[0].margin_to_second_bps == 800
    verification = CandidateDecisionVerificationInputs(
        quality_bundle_ref=bundle_ref,
        quality_bundle=bundle,
        candidate_sets=(candidates,),
        policy=target_quality_policy(),
        current_context=value.gate_context,
        verified_at=NOW,
        authority=evidence,
        authority_ledger=ledger,
        quality_resolver=_CurrentMediaResolver(),
        evaluation_verifier=_CurrentEvaluationVerifier(),
        confidence_verifier=_CurrentConfidenceVerifier(),
    )
    assert verify_candidate_decision(value, verification) is value


def test_tie_low_margin_low_confidence_and_missing_authority_escalate() -> None:
    tie, *_ = _decision(scores=(9000, 9000))
    assert tie.status is CandidateDecisionStatus.ESCALATION_REQUIRED
    assert "selection.margin.tie" in tie.reason_codes

    low_margin, *_ = _decision(scores=(9000, 8600))
    assert "selection.margin.below_threshold" in low_margin.reason_codes

    low_confidence, *_ = _decision(confidence=(8499, 9000))
    assert "selection.confidence.below_threshold" in low_confidence.reason_codes

    missing, *_ = _decision(authority=False)
    assert "selection.authority.missing" in missing.reason_codes
    assert missing.shots[0].selected_subject is None


def test_exact_thresholds_pass_and_current_evidence_failures_deny() -> None:
    exact, *_ = _decision(scores=(8000, 7500), confidence=(8500, 9000))
    assert exact.status is CandidateDecisionStatus.AUTO_SELECTED
    assert exact.shots[0].margin_to_second_bps == 500

    below_score, *_ = _decision(scores=(7999, 7400))
    assert below_score.status is CandidateDecisionStatus.ESCALATION_REQUIRED
    assert "selection.score.below_threshold" in below_score.reason_codes

    value, bundle_ref, bundle, candidates, evidence, ledger = _decision()

    class StaleMediaResolver(_CurrentMediaResolver):
        def resolve_current(self, subject, *, evaluated_at):
            return replace(subject, byte_length=subject.byte_length + 1)

    denied = build_candidate_decision(
        episode_id="episode-a",
        quality_bundle_ref=bundle_ref,
        quality_bundle=bundle,
        candidate_sets=(candidates,),
        policy=target_quality_policy(),
        current_context=value.gate_context,
        evaluated_at=NOW,
        authority=evidence,
        authority_ledger=ledger,
        quality_resolver=StaleMediaResolver(),
        evaluation_verifier=_CurrentEvaluationVerifier(),
        confidence_verifier=_CurrentConfidenceVerifier(),
    )
    assert denied.status is CandidateDecisionStatus.DENIED
    assert "selection.quality.current_evidence_invalid" in denied.reason_codes

    unverified_authority = build_candidate_decision(
        episode_id="episode-a",
        quality_bundle_ref=bundle_ref,
        quality_bundle=bundle,
        candidate_sets=(candidates,),
        policy=target_quality_policy(),
        current_context=value.gate_context,
        evaluated_at=NOW,
        authority=evidence,
        authority_ledger=None,
        quality_resolver=_CurrentMediaResolver(),
        evaluation_verifier=_CurrentEvaluationVerifier(),
        confidence_verifier=_CurrentConfidenceVerifier(),
    )
    assert "selection.authority.missing" in unverified_authority.reason_codes


def test_hard_failure_denies_even_with_high_score() -> None:
    value, *_ = _decision(scores=(10_000, 8200), hard_fail=True)
    assert value.status is CandidateDecisionStatus.DENIED
    assert "selection.quality.hard_failure" in value.reason_codes
    assert value.shots[0].selected_subject is None

    non_winner_failure, *_ = _decision(
        scores=(9000, 10_000),
        hard_fail=True,
    )
    assert non_winner_failure.status is CandidateDecisionStatus.DENIED
    assert "selection.quality.hard_failure" in non_winner_failure.reason_codes


def test_every_confidence_receipt_is_current_and_authority_binds_full_input() -> None:
    verifier = _CurrentConfidenceVerifier(stale_call=2)
    stale, *_ = _decision(confidence_verifier=verifier)
    assert stale.status is CandidateDecisionStatus.DENIED
    assert "selection.confidence.current_evidence_invalid" in stale.reason_codes
    assert verifier.calls == 2

    value, bundle_ref, bundle, candidates, evidence, ledger = _decision(
        confidence=(8400, 9000)
    )
    modified = replace(
        candidates,
        candidates=(
            replace(candidates.candidates[0], confidence_bps=9000),
            candidates.candidates[1],
        ),
    )
    rebound = build_candidate_decision(
        episode_id="episode-a",
        quality_bundle_ref=bundle_ref,
        quality_bundle=bundle,
        candidate_sets=(modified,),
        policy=target_quality_policy(),
        current_context=value.gate_context,
        evaluated_at=NOW,
        authority=evidence,
        authority_ledger=ledger,
        quality_resolver=_CurrentMediaResolver(),
        evaluation_verifier=_CurrentEvaluationVerifier(),
        confidence_verifier=_CurrentConfidenceVerifier(),
    )
    assert rebound.status is not CandidateDecisionStatus.AUTO_SELECTED
    assert "selection.authority.invalid" in rebound.reason_codes

    legacy = _request("rank_generation_candidates")
    legacy_inputs = tuple(
        sorted(
            (
                bundle_ref,
                *(item.subject.reference for item in candidates.candidates),
                *(item.confidence_receipt_ref for item in candidates.candidates),
            ),
            key=lambda item: (
                str(item.path).casefold(),
                str(item.path),
                str(item.sha256),
                str(item.artifact_version),
            ),
        )
    )
    legacy_request = build_action_authority_request(
        request_id="legacy-ranking-request",
        request_envelope_sha256=str(value.selection_input_sha256),
        idempotency_key="legacy-ranking",
        requester_principal_id="requester-a",
        plan=legacy.plan,
        workflow_evaluation=legacy.workflow_evaluation,
        profiles=legacy.profiles,
        scope=replace(
            legacy.scope,
            provider_id=None,
            model_id=None,
            destination=None,
            cost_minor_units=0,
            candidate_count=2,
            input_artifacts=legacy_inputs,
            allowed_outputs=(),
        ),
        hard_escalation_facts=legacy.hard_escalation_facts,
    )
    legacy_ledger = FakeLedger(AuthoritySource.POLICY, ())
    legacy_risk, legacy_decision = evaluate_authority(
        legacy_request,
        target_policy_bundle(),
        ledger=legacy_ledger,
        evaluated_at=NOW,
    )
    legacy_receipt = legacy_ledger._receipt(
        legacy_request,
        legacy_risk.assessment_sha256,
        purpose=VerificationPurpose.INITIAL_DECISION,
        evaluated_at=NOW,
    )
    legacy_evidence = InitialAuthorityEvidence(
        legacy_request,
        legacy_risk,
        legacy_decision,
        legacy_receipt,
    )
    legacy_rebound = build_candidate_decision(
        episode_id="episode-a",
        quality_bundle_ref=bundle_ref,
        quality_bundle=bundle,
        candidate_sets=(candidates,),
        policy=target_quality_policy(),
        current_context=value.gate_context,
        evaluated_at=NOW,
        authority=legacy_evidence,
        authority_ledger=legacy_ledger,
        quality_resolver=_CurrentMediaResolver(),
        evaluation_verifier=_CurrentEvaluationVerifier(),
        confidence_verifier=_CurrentConfidenceVerifier(),
    )
    assert legacy_rebound.status is not CandidateDecisionStatus.AUTO_SELECTED
    assert "selection.authority.invalid" in legacy_rebound.reason_codes


def test_candidate_decision_reverifies_authority_at_current_time() -> None:
    value, bundle_ref, bundle, candidates, evidence, ledger = _decision()
    later = NOW + timedelta(seconds=1)
    risk, decision = evaluate_authority(
        evidence.request,
        target_policy_bundle(),
        ledger=ledger,
        evaluated_at=later,
    )
    receipt = ledger._receipt(
        evidence.request,
        risk.assessment_sha256,
        purpose=VerificationPurpose.INITIAL_DECISION,
        evaluated_at=later,
    )
    current_evidence = InitialAuthorityEvidence(
        evidence.request,
        risk,
        decision,
        receipt,
    )
    verification = CandidateDecisionVerificationInputs(
        quality_bundle_ref=bundle_ref,
        quality_bundle=bundle,
        candidate_sets=(candidates,),
        policy=target_quality_policy(),
        current_context=value.gate_context,
        verified_at=later,
        authority=current_evidence,
        authority_ledger=ledger,
        quality_resolver=_CurrentMediaResolver(),
        evaluation_verifier=_CurrentEvaluationVerifier(),
        confidence_verifier=_CurrentConfidenceVerifier(),
    )
    assert verify_candidate_decision(value, verification) is value

    ledger.ledger_state = LedgerRecordState.REVOKED
    with pytest.raises(SelectionContractError):
        verify_candidate_decision(value, verification)
