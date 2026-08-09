from __future__ import annotations

from dataclasses import replace

from video_factory.authority import (
    AuthorityDecisionStatus,
    AuthoritySource,
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
    CandidateDecisionStatus,
    CandidateDecisionVerificationInputs,
    CandidateOption,
    ShotCandidateSet,
    build_candidate_decision,
    verify_candidate_decision,
)
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


def _authority(bundle_ref, subjects):
    base = _request("rank_generation_candidates")
    inputs = tuple(
        sorted(
            (bundle_ref, *(subject.reference for subject in subjects)),
            key=lambda item: (
                str(item.path).casefold(),
                str(item.path),
                str(item.sha256),
                str(item.artifact_version),
            ),
        )
    )
    request = _request(
        "rank_generation_candidates",
        scope=replace(
            base.scope,
            provider_id=None,
            model_id=None,
            destination=None,
            input_artifacts=inputs,
        ),
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


def _decision(scores=(9000, 8200), confidence=(9000, 9000), *, authority=True, hard_fail=False):
    base = _request("rank_generation_candidates")
    subjects, bundle = _bundle(base.gate_context, scores, hard_fail=hard_fail)
    bundle_ref = _ref("quality/bundle.json", "0", "quality-bundle/1.0")
    bundle_ref = replace(bundle_ref, sha256=quality_bundle_bytes_sha256(bundle))
    candidate_set = ShotCandidateSet(
        OpaqueId("shot-a"),
        tuple(
            CandidateOption(subject, OpaqueId(f"adapter-{index}"), confidence[index])
            for index, subject in enumerate(subjects)
        ),
    )
    evidence, ledger = _authority(bundle_ref, subjects) if authority else (None, None)
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
        evaluated_at=NOW,
        authority=evidence,
        authority_ledger=ledger,
        quality_resolver=_CurrentMediaResolver(),
        evaluation_verifier=_CurrentEvaluationVerifier(),
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
