from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from video_factory.authority import (
    AuthorityDecisionStatus,
    AuthoritySource,
    LedgerRecordState,
    VerificationPurpose,
    evaluate_authority,
    target_policy_bundle,
)
from video_factory.domain import ArtifactReference, ArtifactVersion, HashDigest, OpaqueId, RelativeArtifactPath
from video_factory.config import canonical_sha256
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
from video_factory.release import (
    ReleaseAssessmentStatus,
    ReleaseAuthorityEvidence,
    ReleaseContractError,
    ReleaseCandidateVerificationInputs,
    ReleaseVisibility,
    assess_release_candidate,
    build_destination_binding,
    build_release_candidate,
    destination_binding_bytes_sha256,
    release_candidate_bytes_sha256,
    release_candidate_to_mapping,
    release_assessment_to_mapping,
    verify_release_candidate,
    verify_release_assessment,
)
from video_factory.selection import candidate_decision_bytes_sha256
from video_factory.selection import CandidateDecisionVerificationInputs
from tests.unit.test_authority_control import FakeLedger, NOW, _ref as authority_ref, _request
from tests.unit.test_candidate_decision import _CurrentConfidenceVerifier, _decision
from tests.unit.test_quality_bundle import (
    _CurrentEvaluationVerifier,
    _CurrentMediaResolver,
)
from video_factory.release.decision import _candidate_identity, _candidate_material
from video_factory.selection.decision import _identity as _selection_identity


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
        workspace_id=str(candidate_decision.workspace_id),
        channel_id=str(candidate_decision.channel_id),
        concept_id=str(candidate_decision.concept_id),
        episode_id=str(candidate_decision.episode_id),
        quality_origin_evaluated_at=selection_bundle.evaluated_at,
        quality_bundle_ref=selection_bundle_ref,
        quality_bundle=selection_bundle,
        candidate_sets=(selection_candidates,),
        policy=target_quality_policy(),
        current_context=candidate_decision.gate_context,
        verified_at=NOW,
        origin_evaluated_at=NOW,
        origin_authority=selection_authority,
        origin_authority_ledger=selection_ledger,
        authority=selection_authority,
        authority_ledger=selection_ledger,
        quality_resolver=resolver,
        evaluation_verifier=_CurrentEvaluationVerifier(),
        confidence_verifier=_CurrentConfidenceVerifier(),
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
        workspace_id=str(candidate_decision.workspace_id),
        channel_id=str(candidate_decision.channel_id),
        concept_id=str(candidate_decision.concept_id),
        episode_id="episode-a",
        quality_origin_evaluated_at=bundle.evaluated_at,
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
    verification = ReleaseCandidateVerificationInputs(
        workspace_id=str(candidate_decision.workspace_id),
        channel_id=str(candidate_decision.channel_id),
        concept_id=str(candidate_decision.concept_id),
        episode_id="episode-a",
        created_at=NOW.isoformat(),
        quality_origin_evaluated_at=bundle.evaluated_at,
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
        verified_at=NOW,
        quality_resolver=resolver,
        evaluation_verifier=_CurrentEvaluationVerifier(),
    )
    return candidate, candidate_ref, destination, verification


def _authority(
    candidate,
    candidate_ref,
    destination,
    *,
    granted: bool,
    evaluated_at=NOW,
    scope_overrides=None,
):
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
            **(scope_overrides or {}),
        ),
    )
    if not granted:
        risk, decision = evaluate_authority(
            request,
            target_policy_bundle(),
            ledger=None,
            evaluated_at=evaluated_at,
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
        evaluated_at=evaluated_at,
    )
    assert decision.status is AuthorityDecisionStatus.AUTHORIZED
    receipt = ledger._receipt(
        request,
        risk.assessment_sha256,
        purpose=VerificationPurpose.INITIAL_DECISION,
        evaluated_at=evaluated_at,
    )
    assert receipt.receipt_sha256 == decision.verification_receipt_sha256
    return (
        ReleaseAuthorityEvidence(request, risk, decision, receipt),
        ledger,
        authority_references,
    )


def _current_release_verification(verification, evaluated_at):
    prior = verification.candidate_verification
    ledger = prior.authority_ledger
    risk, decision = evaluate_authority(
        prior.authority.request,
        target_policy_bundle(),
        ledger=ledger,
        evaluated_at=evaluated_at,
    )
    receipt = ledger._receipt(
        prior.authority.request,
        risk.assessment_sha256,
        purpose=VerificationPurpose.INITIAL_DECISION,
        evaluated_at=evaluated_at,
    )
    current_authority = InitialAuthorityEvidence(
        prior.authority.request,
        risk,
        decision,
        receipt,
    )
    return replace(
        verification,
        verified_at=evaluated_at,
        candidate_verification=replace(
            prior,
            verified_at=evaluated_at,
            authority=current_authority,
        ),
    )


def _decision_at(value, evaluated_at):
    rebound = replace(value, evaluated_at=evaluated_at.isoformat())
    digest = canonical_sha256(_selection_identity(rebound))
    return replace(
        rebound,
        decision_id=OpaqueId(f"candidate-decision-{str(digest)[:20]}"),
        decision_sha256=digest,
    )


def test_release_requires_one_human_without_synthesizing_evidence() -> None:
    candidate, candidate_ref, destination, verification = _release_fixture()
    authority, ledger, authority_references = _authority(
        candidate, candidate_ref, destination, granted=False
    )
    assessment = assess_release_candidate(
        release_candidate_ref=candidate_ref,
        release_candidate=candidate,
        release_candidate_verification=verification,
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
    candidate, candidate_ref, destination, verification = _release_fixture()
    authority, ledger, authority_references = _authority(
        candidate, candidate_ref, destination, granted=True
    )
    assessment = assess_release_candidate(
        release_candidate_ref=candidate_ref,
        release_candidate=candidate,
        release_candidate_verification=verification,
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
        release_candidate_verification=verification,
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
            release_candidate_verification=verification,
            destination=destination,
            current_context=candidate.gate_context,
            evaluated_at=NOW,
            authority=authority,
            authority_ledger=None,
            authority_references=authority_references,
        )


def test_release_destination_or_context_rebound_fails_closed() -> None:
    candidate, candidate_ref, destination, verification = _release_fixture()
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
            release_candidate_verification=verification,
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
            release_candidate_verification=verification,
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


def test_release_assessment_requires_clean_current_candidate_reverification() -> None:
    candidate, candidate_ref, destination, verification = _release_fixture()
    later = NOW + timedelta(seconds=1)
    current = _current_release_verification(verification, later)
    authority, ledger, authority_references = _authority(
        candidate,
        candidate_ref,
        destination,
        granted=True,
        evaluated_at=later,
    )
    assessment = assess_release_candidate(
        release_candidate_ref=candidate_ref,
        release_candidate=candidate,
        release_candidate_verification=current,
        destination=destination,
        current_context=candidate.gate_context,
        evaluated_at=later,
        authority=authority,
        authority_ledger=ledger,
        authority_references=authority_references,
    )
    assert assessment.status is ReleaseAssessmentStatus.READY

    with pytest.raises(ReleaseContractError, match="freshly verified"):
        assess_release_candidate(
            release_candidate_ref=candidate_ref,
            release_candidate=candidate,
            release_candidate_verification=verification,
            destination=destination,
            current_context=candidate.gate_context,
            evaluated_at=later,
            authority=authority,
            authority_ledger=ledger,
            authority_references=authority_references,
        )

    current.candidate_verification.authority_ledger.ledger_state = (
        LedgerRecordState.REVOKED
    )
    with pytest.raises(ReleaseContractError, match="current trusted evidence"):
        verify_release_candidate(candidate, current)


def test_release_semantic_verifier_rejects_self_rehashed_lineage() -> None:
    candidate, candidate_ref, destination, verification = _release_fixture()
    forged = replace(candidate, quality_bundle_sha256=HashDigest("f" * 64))
    forged = replace(
        forged,
        release_intent_sha256=canonical_sha256(_candidate_material(forged)),
    )
    digest = canonical_sha256(_candidate_identity(forged))
    forged = replace(
        forged,
        candidate_id=OpaqueId(f"release-candidate-{str(digest)[:20]}"),
        candidate_sha256=digest,
    )
    release_candidate_to_mapping(forged)
    with pytest.raises(ReleaseContractError, match="clean current recomputation"):
        verify_release_candidate(forged, verification)

    with pytest.raises(ReleaseContractError, match="exactly one subtitle"):
        release_candidate_to_mapping(
            replace(candidate, subtitle_accessibility_refs=())
        )

    authority, ledger, authority_references = _authority(
        candidate, candidate_ref, destination, granted=True
    )
    assessment = assess_release_candidate(
        release_candidate_ref=candidate_ref,
        release_candidate=candidate,
        release_candidate_verification=verification,
        destination=destination,
        current_context=candidate.gate_context,
        evaluated_at=NOW,
        authority=authority,
        authority_ledger=ledger,
        authority_references=authority_references,
    )
    with pytest.raises(ReleaseContractError, match="single-human"):
        release_assessment_to_mapping(
            replace(assessment, required_independent_humans=True)
        )


def test_release_causal_time_order_is_fail_closed() -> None:
    candidate, _, _, verification = _release_fixture()
    created_later = NOW + timedelta(seconds=2)
    current = _current_release_verification(verification, created_later)
    decision_after_quality = _decision_at(
        verification.candidate_decision,
        NOW + timedelta(seconds=1),
    )
    decision_ref = replace(
        verification.candidate_decision_ref,
        sha256=candidate_decision_bytes_sha256(decision_after_quality),
    )
    with pytest.raises(ReleaseContractError, match="current trusted evidence"):
        build_release_candidate(
            workspace_id=verification.workspace_id,
            channel_id=verification.channel_id,
            concept_id=verification.concept_id,
            episode_id=verification.episode_id,
            quality_origin_evaluated_at=verification.quality_origin_evaluated_at,
            final_media=verification.final_media,
            metadata_ref=verification.metadata_ref,
            subtitle_accessibility_refs=verification.subtitle_accessibility_refs,
            thumbnail=verification.thumbnail,
            quality_bundle_ref=verification.quality_bundle_ref,
            quality_bundle=verification.quality_bundle,
            candidate_decision_ref=decision_ref,
            candidate_decision=decision_after_quality,
            candidate_verification=current.candidate_verification,
            destination_ref=verification.destination_ref,
            destination=verification.destination,
            policy=verification.policy,
            current_context=verification.current_context,
            created_at=created_later.isoformat(),
            quality_resolver=verification.quality_resolver,
            evaluation_verifier=verification.evaluation_verifier,
        )

    future_quality = build_quality_bundle(
        episode_id=str(verification.quality_bundle.episode_id),
        gate_context=verification.current_context,
        evaluations=verification.quality_bundle.evaluations,
        policy=verification.policy,
        evaluated_at=(NOW + timedelta(seconds=1)).isoformat(),
    )
    future_quality_ref = replace(
        verification.quality_bundle_ref,
        sha256=quality_bundle_bytes_sha256(future_quality),
    )
    with pytest.raises(ReleaseContractError, match="current trusted evidence"):
        build_release_candidate(
            workspace_id=verification.workspace_id,
            channel_id=verification.channel_id,
            concept_id=verification.concept_id,
            episode_id=verification.episode_id,
            quality_origin_evaluated_at=future_quality.evaluated_at,
            final_media=verification.final_media,
            metadata_ref=verification.metadata_ref,
            subtitle_accessibility_refs=verification.subtitle_accessibility_refs,
            thumbnail=verification.thumbnail,
            quality_bundle_ref=future_quality_ref,
            quality_bundle=future_quality,
            candidate_decision_ref=verification.candidate_decision_ref,
            candidate_decision=verification.candidate_decision,
            candidate_verification=verification.candidate_verification,
            destination_ref=verification.destination_ref,
            destination=verification.destination,
            policy=verification.policy,
            current_context=verification.current_context,
            created_at=NOW.isoformat(),
            quality_resolver=verification.quality_resolver,
            evaluation_verifier=verification.evaluation_verifier,
        )


@pytest.mark.parametrize(
    "scope_field",
    ("workspace_id", "channel_id", "concept_id", "episode_id"),
)
def test_release_authority_requires_exact_production_scope(scope_field: str) -> None:
    candidate, candidate_ref, destination, verification = _release_fixture()
    authority, ledger, authority_references = _authority(
        candidate,
        candidate_ref,
        destination,
        granted=True,
        scope_overrides={scope_field: OpaqueId(f"foreign-{scope_field}")},
    )
    with pytest.raises(ReleaseContractError, match="another action"):
        assess_release_candidate(
            release_candidate_ref=candidate_ref,
            release_candidate=candidate,
            release_candidate_verification=verification,
            destination=destination,
            current_context=candidate.gate_context,
            evaluated_at=NOW,
            authority=authority,
            authority_ledger=ledger,
            authority_references=authority_references,
        )


def test_release_candidate_creation_time_is_independently_bound() -> None:
    candidate, _, _, verification = _release_fixture()
    forged_created_at = (NOW + timedelta(seconds=1)).isoformat()
    provisional = replace(
        candidate,
        candidate_id=OpaqueId("pending"),
        candidate_sha256=HashDigest("0" * 64),
        release_intent_sha256=HashDigest("0" * 64),
        created_at=forged_created_at,
    )
    provisional = replace(
        provisional,
        release_intent_sha256=canonical_sha256(_candidate_material(provisional)),
    )
    digest = canonical_sha256(_candidate_identity(provisional))
    forged = replace(
        provisional,
        candidate_id=OpaqueId(f"release-candidate-{str(digest)[:20]}"),
        candidate_sha256=digest,
    )
    current = _current_release_verification(
        verification,
        NOW + timedelta(seconds=2),
    )
    with pytest.raises(ReleaseContractError, match="clean current recomputation"):
        verify_release_candidate(forged, current)
