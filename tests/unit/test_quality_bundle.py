from __future__ import annotations

from dataclasses import replace

import pytest

from video_factory.approvals import GateContext
from video_factory.authority import target_policy_bundle
from video_factory.domain import ArtifactReference, ArtifactVersion, HashDigest, OpaqueId, RelativeArtifactPath
from video_factory.quality import (
    MediaSubject,
    QualityBundleStatus,
    QualityContractError,
    QualityDimension,
    QualityVerdict,
    RemediationStatus,
    build_dimension_evaluation,
    build_quality_bundle,
    plan_targeted_remediation,
    remediation_history_entry,
    target_quality_policy,
    validate_quality_policy,
    validate_quality_bundle,
    verify_quality_bundle,
)


def _ref(path: str, digest: str, version: str) -> ArtifactReference:
    return ArtifactReference(
        RelativeArtifactPath(path),
        HashDigest(digest * 64),
        ArtifactVersion(version),
    )


def _context() -> GateContext:
    return GateContext(
        HashDigest("1" * 64),
        target_policy_bundle().bundle_sha256,
        HashDigest("3" * 64),
        HashDigest("4" * 64),
        HashDigest("5" * 64),
        HashDigest("6" * 64),
        HashDigest("7" * 64),
    )


def _subject(name: str = "a") -> MediaSubject:
    return MediaSubject(
        shot_id=OpaqueId(f"shot-{name}"),
        component_id=OpaqueId(f"component-{name}"),
        reference=_ref(f"media/{name}.mp4", "a", "generated-media/1.0"),
        byte_length=1234,
        workspace_observation_sha256=HashDigest("b" * 64),
        observation_receipt_ref=_ref(
            f"evidence/{name}-observation.json",
            "c",
            "quality-observation-receipt/1.0",
        ),
    )


class _CurrentMediaResolver:
    def resolve_current(self, subject, *, evaluated_at):
        return subject


class _CurrentEvaluationVerifier:
    def verify_current(
        self,
        evaluation,
        *,
        gate_context,
        policy,
        evaluated_at,
    ):
        return evaluation


def _evaluations(
    subject: MediaSubject,
    *,
    failing: QualityDimension | None = None,
    score: int = 9000,
):
    policy = target_quality_policy()
    context = _context()
    values = []
    for index, dimension in enumerate(QualityDimension):
        fail = dimension is failing
        values.append(
            build_dimension_evaluation(
                dimension=dimension,
                subject=subject,
                evaluator_id=f"evaluator-{index}",
                evaluator_version="1.0",
                evaluator_receipt_ref=_ref(
                    f"evidence/{subject.shot_id}-{dimension.value}.json",
                    hex(index + 1)[2:],
                    "quality-evaluation-receipt/1.0",
                ),
                gate_context=context,
                policy=policy,
                verdict=QualityVerdict.FAIL if fail else QualityVerdict.PASS,
                score_bps=score if not fail else 10_000,
                hard_failure=fail,
                reason_codes=(f"quality.{dimension.value}.failed",) if fail else (),
            )
        )
    return tuple(values)


def test_quality_bundle_requires_all_nine_independent_dimensions() -> None:
    policy = target_quality_policy()
    evaluations = _evaluations(_subject())
    bundle = build_quality_bundle(
        episode_id="episode-a",
        gate_context=_context(),
        evaluations=evaluations,
        policy=policy,
        evaluated_at="2026-08-09T10:00:00+00:00",
    )
    assert bundle.status is QualityBundleStatus.PASSED
    assert len(bundle.evaluations) == 9
    assert validate_quality_bundle(bundle) is bundle

    with pytest.raises(QualityContractError, match="exactly one evaluation"):
        build_quality_bundle(
            episode_id="episode-a",
            gate_context=_context(),
            evaluations=evaluations[:-1],
            policy=policy,
            evaluated_at="2026-08-09T10:00:00+00:00",
        )

    duplicated_evaluator = replace(
        evaluations[-1],
        evaluator_id=evaluations[0].evaluator_id,
    )
    with pytest.raises(QualityContractError, match="digest mismatch"):
        build_quality_bundle(
            episode_id="episode-a",
            gate_context=_context(),
            evaluations=(*evaluations[:-1], duplicated_evaluator),
            policy=policy,
            evaluated_at="2026-08-09T10:00:00+00:00",
        )


def test_hard_failure_cannot_be_averaged_away() -> None:
    bundle = build_quality_bundle(
        episode_id="episode-a",
        gate_context=_context(),
        evaluations=_evaluations(
            _subject(),
            failing=QualityDimension.CONTINUITY,
            score=10_000,
        ),
        policy=target_quality_policy(),
        evaluated_at="2026-08-09T10:00:00+00:00",
    )
    assert bundle.subjects[0].aggregate_score_bps == 10_000
    assert bundle.status is QualityBundleStatus.BLOCKED
    assert bundle.hard_failure_reason_codes == ("quality.continuity.failed",)


def test_quality_contract_rejects_non_boolean_flags_and_policy_booleans() -> None:
    with pytest.raises(QualityContractError, match="must be booleans"):
        build_dimension_evaluation(
            dimension=QualityDimension.AUDIO,
            subject=_subject(),
            evaluator_id="evaluator-audio",
            evaluator_version="1.0",
            evaluator_receipt_ref=_ref(
                "evidence/audio.json",
                "d",
                "quality-evaluation-receipt/1.0",
            ),
            gate_context=_context(),
            policy=target_quality_policy(),
            verdict=QualityVerdict.FAIL,
            score_bps=0,
            hard_failure="false",  # type: ignore[arg-type]
            reason_codes=("quality.audio.failed",),
        )

    malformed_policy = replace(
        target_quality_policy(),
        require_one_human_release_approval=1,  # type: ignore[arg-type]
    )
    with pytest.raises(QualityContractError, match="fail closed"):
        validate_quality_policy(malformed_policy)


def test_targeted_remediation_is_bounded_and_preserves_lineage() -> None:
    policy = target_quality_policy()
    failed = build_quality_bundle(
        episode_id="episode-a",
        gate_context=_context(),
        evaluations=_evaluations(_subject(), failing=QualityDimension.CONTINUITY),
        policy=policy,
        evaluated_at="2026-08-09T10:00:00+00:00",
    )
    plan = plan_targeted_remediation(failed, policy=policy)
    assert plan.status is RemediationStatus.READY
    assert plan.full_pipeline_rerun is False
    assert len(plan.targets) == 1
    assert plan.targets[0].dimensions == (QualityDimension.CONTINUITY,)
    assert plan.targets[0].component_id == failed.subjects[0].subject.component_id

    first = remediation_history_entry(
        failed,
        attempt_index=0,
        attempt_receipt_ref=_ref(
            "evidence/remediation-attempt-0.json",
            "e",
            "remediation-attempt-receipt/1.0",
        ),
        cumulative_cost_minor_units=0,
        candidate_count=1,
    )
    oscillating = plan_targeted_remediation(
        failed,
        history=(first,),
        policy=policy,
        cumulative_cost_minor_units=10,
        candidate_count=2,
    )
    assert oscillating.status is RemediationStatus.ESCALATION_REQUIRED
    assert "quality.remediation.replay" in oscillating.reason_codes
    assert oscillating.targets == ()
    assert oscillating.predecessor_receipt_ref == first.attempt_receipt_ref


def test_remediation_rejects_usage_regression_and_duplicate_receipts() -> None:
    policy = target_quality_policy()
    failed = build_quality_bundle(
        episode_id="episode-a",
        gate_context=_context(),
        evaluations=_evaluations(_subject(), failing=QualityDimension.CONTINUITY),
        policy=policy,
        evaluated_at="2026-08-09T10:00:00+00:00",
    )
    receipt = _ref(
        "evidence/remediation-attempt.json",
        "f",
        "remediation-attempt-receipt/1.0",
    )
    first = remediation_history_entry(
        failed,
        attempt_index=0,
        attempt_receipt_ref=receipt,
        cumulative_cost_minor_units=10,
        candidate_count=2,
    )
    with pytest.raises(QualityContractError, match="cannot decrease"):
        plan_targeted_remediation(
            failed,
            history=(first,),
            policy=policy,
            cumulative_cost_minor_units=9,
            candidate_count=2,
        )
    second = replace(first, attempt_index=1)
    with pytest.raises(QualityContractError, match="duplicated"):
        plan_targeted_remediation(
            failed,
            history=(first, second),
            policy=policy,
            cumulative_cost_minor_units=10,
            candidate_count=2,
        )


def test_media_observation_is_material_to_quality_identity() -> None:
    original = _evaluations(_subject())
    changed_subject = replace(original[0].subject, byte_length=1235)
    changed = build_dimension_evaluation(
        dimension=original[0].dimension,
        subject=changed_subject,
        evaluator_id=str(original[0].evaluator_id),
        evaluator_version=original[0].evaluator_version,
        evaluator_receipt_ref=original[0].evaluator_receipt_ref,
        gate_context=_context(),
        policy=target_quality_policy(),
        verdict=original[0].verdict,
        score_bps=original[0].score_bps,
    )
    with pytest.raises(QualityContractError, match="exactly one evaluation"):
        build_quality_bundle(
            episode_id="episode-a",
            gate_context=_context(),
            evaluations=(changed, *original[1:]),
            policy=target_quality_policy(),
            evaluated_at="2026-08-09T10:00:00+00:00",
        )


def test_production_verifier_reresolves_current_exact_media() -> None:
    policy = target_quality_policy()
    bundle = build_quality_bundle(
        episode_id="episode-a",
        gate_context=_context(),
        evaluations=_evaluations(_subject()),
        policy=policy,
        evaluated_at="2026-08-09T10:00:00+00:00",
    )

    class Resolver:
        def __init__(self, changed: bool) -> None:
            self.changed = changed

        def resolve_current(self, subject, *, evaluated_at):
            assert evaluated_at == "2026-08-09T10:01:00+00:00"
            return replace(subject, byte_length=subject.byte_length + 1) if self.changed else subject

    class CountingVerifier(_CurrentEvaluationVerifier):
        def __init__(self, stale_call: int | None = None) -> None:
            self.calls = 0
            self.stale_call = stale_call

        def verify_current(self, evaluation, **kwargs):
            self.calls += 1
            if self.calls == self.stale_call:
                return replace(evaluation, evaluator_version="stale")
            return evaluation

    verifier = CountingVerifier()
    assert verify_quality_bundle(
        bundle,
        current_context=_context(),
        policy=policy,
        resolver=Resolver(False),
        evaluation_verifier=verifier,
        evaluated_at="2026-08-09T10:01:00+00:00",
    ) is bundle
    assert verifier.calls == len(tuple(QualityDimension)) == 9
    with pytest.raises(QualityContractError, match="current exact media"):
        verify_quality_bundle(
            bundle,
            current_context=_context(),
            policy=policy,
            resolver=Resolver(True),
            evaluation_verifier=_CurrentEvaluationVerifier(),
            evaluated_at="2026-08-09T10:01:00+00:00",
        )

    class ReboundVerifier(_CurrentEvaluationVerifier):
        def verify_current(self, evaluation, **kwargs):
            return replace(evaluation, evaluator_version="other")

    with pytest.raises(QualityContractError, match="missing, stale, revoked"):
        verify_quality_bundle(
            bundle,
            current_context=_context(),
            policy=policy,
            resolver=Resolver(False),
            evaluation_verifier=ReboundVerifier(),
            evaluated_at="2026-08-09T10:01:00+00:00",
        )

    non_first = CountingVerifier(stale_call=8)
    with pytest.raises(QualityContractError, match="missing, stale, revoked"):
        verify_quality_bundle(
            bundle,
            current_context=_context(),
            policy=policy,
            resolver=Resolver(False),
            evaluation_verifier=non_first,
            evaluated_at="2026-08-09T10:01:00+00:00",
        )
    assert non_first.calls == 8
