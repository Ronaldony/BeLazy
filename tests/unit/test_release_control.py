from __future__ import annotations

from dataclasses import replace

import pytest

from video_factory.authority import (
    AuthorityDecisionStatus,
    AuthoritySource,
    VerificationPurpose,
    evaluate_authority,
    target_policy_bundle,
)
from video_factory.domain import ArtifactReference, ArtifactVersion, HashDigest, OpaqueId, RelativeArtifactPath
from video_factory.quality import (
    MediaSubject,
    QualityDimension,
    QualityVerdict,
    build_dimension_evaluation,
    build_quality_bundle,
    quality_bundle_bytes_sha256,
    target_quality_policy,
)
from video_factory.release import (
    ReleaseAssessmentStatus,
    ReleaseAuthorityEvidence,
    ReleaseContractError,
    ReleaseVisibility,
    assess_release_candidate,
    build_destination_binding,
    build_release_candidate,
    destination_binding_bytes_sha256,
    release_candidate_bytes_sha256,
    verify_release_assessment,
)
from video_factory.selection import candidate_decision_bytes_sha256
from video_factory.selection import CandidateDecisionVerificationInputs
from tests.unit.test_authority_control import FakeLedger, NOW, _ref as authority_ref, _request
from tests.unit.test_candidate_decision import _decision
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


def _media(name: str, version: str, digest: str) -> MediaSubject:
    return MediaSubject(
        shot_id=OpaqueId("shot-final"),
        component_id=OpaqueId(f"component-{name}"),
        reference=_ref(f"release/{name}", digest, version),
        byte_length=10_000,
        workspace_observation_sha256=HashDigest("e" * 64),
        observation_receipt_ref=_ref(
            f"evidence/{name}.json",
            digest,
            "quality-observation-receipt/1.0",
        ),
    )


def _release_fixture(*, quality_resolver=None):
    resolver = quality_resolver or _CurrentMediaResolver()
    (
        candidate_decision,
        selection_bundle_ref,
        selection_bundle,
        selection_candidates,
        selection_authority,
        selection_ledger,
    ) = _decision()
    candidate_verification = CandidateDecisionVerificationInputs(
        quality_bundle_ref=selection_bundle_ref,
        quality_bundle=selection_bundle,
        candidate_sets=(selection_candidates,),
        policy=target_quality_policy(),
        current_context=candidate_decision.gate_context,
        evaluated_at=NOW,
        authority=selection_authority,
        authority_ledger=selection_ledger,
        quality_resolver=resolver,
        evaluation_verifier=_CurrentEvaluationVerifier(),
    )
    approve = _request("approve_publish")
    final_media = _media("final.mp4", "final-media/1.0", "1")
    thumbnail = _media("thumbnail.png", "thumbnail/1.0", "2")
    policy = target_quality_policy()
    evaluations = tuple(
        build_dimension_evaluation(
            dimension=dimension,
            subject=final_media,
            evaluator_id=f"release-evaluator-{index}",
            evaluator_version="1.0",
            evaluator_receipt_ref=_ref(
                f"evidence/release-{dimension.value}.json",
                format(index + 3, "x"),
                "quality-evaluation-receipt/1.0",
            ),
            gate_context=approve.gate_context,
            policy=policy,
            verdict=QualityVerdict.PASS,
            score_bps=9500,
        )
        for index, dimension in enumerate(QualityDimension)
    )
    bundle = build_quality_bundle(
        episode_id="episode-a",
        gate_context=approve.gate_context,
        evaluations=evaluations,
        policy=policy,
        evaluated_at=NOW.isoformat(),
    )
    bundle_ref = replace(
        _ref("quality/release-bundle.json", "0", "quality-bundle/1.0"),
        sha256=quality_bundle_bytes_sha256(bundle),
    )
    decision_ref = replace(
        _ref("selection/candidate-decision.json", "0", "candidate-decision/1.0"),
        sha256=candidate_decision_bytes_sha256(candidate_decision),
    )
    destination = build_destination_binding(
        platform="platform-a",
        channel_account_id="channel-account-a",
        visibility=ReleaseVisibility.PRIVATE,
        locale="ko-KR",
        region="KR",
        scheduled_for=None,
        policy=policy,
    )
    destination_ref = replace(
        _ref("release/destination.json", "0", "destination-binding/1.0"),
        sha256=destination_binding_bytes_sha256(destination),
    )
    metadata = _ref(
        "release/metadata.json",
        "5",
        "publish-metadata-draft/1.0",
    )
    subtitles = (
        _ref("release/accessibility.json", "6", "accessibility-track/1.0"),
        _ref("release/subtitles.vtt", "7", "subtitle-track/1.0"),
    )
    candidate = build_release_candidate(
        episode_id="episode-a",
        final_media=final_media,
        metadata_ref=metadata,
        subtitle_accessibility_refs=subtitles,
        thumbnail=thumbnail,
        quality_bundle_ref=bundle_ref,
        quality_bundle=bundle,
        candidate_decision_ref=decision_ref,
        candidate_decision=candidate_decision,
        candidate_verification=candidate_verification,
        destination_ref=destination_ref,
        destination=destination,
        policy=policy,
        current_context=approve.gate_context,
        created_at=NOW.isoformat(),
        quality_resolver=resolver,
        evaluation_verifier=_CurrentEvaluationVerifier(),
    )
    candidate_ref = replace(
        _ref("release/release-candidate.json", "0", "release-candidate/1.0"),
        sha256=release_candidate_bytes_sha256(candidate),
    )
    return candidate, candidate_ref, destination


def _authority(candidate, candidate_ref, destination, *, granted: bool):
    values = (
        candidate_ref,
        candidate.final_media.reference,
        candidate.metadata_ref,
        *candidate.subtitle_accessibility_refs,
        candidate.thumbnail.reference,
        candidate.quality_bundle_ref,
        candidate.candidate_decision_ref,
        candidate.destination_ref,
    )
    inputs = tuple(
        sorted(
            values,
            key=lambda item: (
                str(item.path).casefold(),
                str(item.path),
                str(item.sha256),
                str(item.artifact_version),
            ),
        )
    )
    base = _request("approve_publish")
    request = _request(
        "approve_publish",
        scope=replace(
            base.scope,
            destination=str(destination.destination_id),
            input_artifacts=inputs,
        ),
    )
    if not granted:
        risk, decision = evaluate_authority(
            request,
            target_policy_bundle(),
            ledger=None,
            evaluated_at=NOW,
        )
        assert decision.status is AuthorityDecisionStatus.HUMAN_APPROVAL_REQUIRED
        return ReleaseAuthorityEvidence(request, risk, decision, None), None, ()
    ledger = FakeLedger(AuthoritySource.ONE_SHOT_HUMAN, ("release-approver",))
    authority_references = (
        authority_ref(
            "ledger/release-approval.json",
            "8",
            "human-approval/1.0",
        ),
    )
    risk, decision = evaluate_authority(
        request,
        target_policy_bundle(),
        ledger=ledger,
        authority_references=authority_references,
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
    return (
        ReleaseAuthorityEvidence(request, risk, decision, receipt),
        ledger,
        authority_references,
    )


def test_release_requires_one_human_without_synthesizing_evidence() -> None:
    candidate, candidate_ref, destination = _release_fixture()
    authority, ledger, authority_references = _authority(
        candidate, candidate_ref, destination, granted=False
    )
    assessment = assess_release_candidate(
        release_candidate_ref=candidate_ref,
        release_candidate=candidate,
        destination=destination,
        current_context=candidate.gate_context,
        evaluated_at=NOW,
        authority=authority,
        authority_ledger=ledger,
        authority_references=authority_references,
    )
    assert assessment.status is ReleaseAssessmentStatus.APPROVAL_REQUIRED
    assert assessment.required_independent_humans == 1
    assert assessment.approval_request is not None
    assert assessment.approval_request.creates_authority is False
    assert assessment.authority_receipt_sha256 is None
    assert assessment.publish_performed is False


def test_exact_current_human_authority_yields_handoff_ready_but_never_publish() -> None:
    candidate, candidate_ref, destination = _release_fixture()
    authority, ledger, authority_references = _authority(
        candidate, candidate_ref, destination, granted=True
    )
    assessment = assess_release_candidate(
        release_candidate_ref=candidate_ref,
        release_candidate=candidate,
        destination=destination,
        current_context=candidate.gate_context,
        evaluated_at=NOW,
        authority=authority,
        authority_ledger=ledger,
        authority_references=authority_references,
    )
    assert assessment.status is ReleaseAssessmentStatus.READY
    assert assessment.approval_request is None
    assert assessment.publish_performed is False
    assert assessment.authority_effect == "none"
    assert verify_release_assessment(
        assessment,
        release_candidate_ref=candidate_ref,
        release_candidate=candidate,
        destination=destination,
        current_context=candidate.gate_context,
        evaluated_at=NOW,
        authority=authority,
        authority_ledger=ledger,
        authority_references=authority_references,
    ) is assessment

    with pytest.raises(ReleaseContractError, match="trusted-ledger receipt"):
        assess_release_candidate(
            release_candidate_ref=candidate_ref,
            release_candidate=candidate,
            destination=destination,
            current_context=candidate.gate_context,
            evaluated_at=NOW,
            authority=authority,
            authority_ledger=None,
            authority_references=authority_references,
        )


def test_release_destination_or_context_rebound_fails_closed() -> None:
    candidate, candidate_ref, destination = _release_fixture()
    other_destination = build_destination_binding(
        platform="platform-b",
        channel_account_id="channel-account-other",
        visibility=ReleaseVisibility.PRIVATE,
        locale="ko-KR",
        region="KR",
        scheduled_for=None,
        policy=target_quality_policy(),
    )
    with pytest.raises(ReleaseContractError, match="another destination"):
        assess_release_candidate(
            release_candidate_ref=candidate_ref,
            release_candidate=candidate,
            destination=other_destination,
            current_context=candidate.gate_context,
            evaluated_at=NOW,
            authority=None,
        )

    stale_context = replace(
        candidate.gate_context,
        current_manifest_sha256=HashDigest("0" * 64),
    )
    with pytest.raises(ReleaseContractError, match="stale"):
        assess_release_candidate(
            release_candidate_ref=candidate_ref,
            release_candidate=candidate,
            destination=destination,
            current_context=stale_context,
            evaluated_at=NOW,
            authority=None,
        )


def test_release_candidate_rechecks_thumbnail_exact_bytes() -> None:
    class StaleThumbnailResolver(_CurrentMediaResolver):
        def resolve_current(self, subject, *, evaluated_at):
            if str(subject.reference.artifact_version) == "thumbnail/1.0":
                return replace(subject, byte_length=subject.byte_length + 1)
            return subject

    with pytest.raises(ReleaseContractError, match="thumbnail"):
        _release_fixture(quality_resolver=StaleThumbnailResolver())
