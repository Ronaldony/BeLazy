from __future__ import annotations

from dataclasses import replace

import pytest

from video_factory.quality import (
    QualityDimension,
    QualitySerializationError,
    QualityContractError,
    build_quality_bundle,
    plan_targeted_remediation,
    quality_artifact_from_bytes,
    quality_artifact_to_bytes,
    target_quality_policy,
)
from video_factory.release import (
    ReleaseContractError,
    ReleaseSerializationError,
    assess_release_candidate,
    release_artifact_from_bytes,
    release_artifact_to_bytes,
)
from video_factory.selection import (
    CandidateDecisionStatus,
    SelectionContractError,
    SelectionSerializationError,
    candidate_decision_from_bytes,
    candidate_decision_to_bytes,
)
from tests.unit.test_authority_control import NOW
from tests.unit.test_candidate_decision import _decision
from tests.unit.test_quality_bundle import _context, _evaluations, _subject
from tests.unit.test_release_control import _authority, _release_fixture


def test_quality_selection_and_release_artifacts_roundtrip_exactly() -> None:
    policy = target_quality_policy()
    bundle = build_quality_bundle(
        episode_id="episode-a",
        gate_context=_context(),
        evaluations=_evaluations(
            _subject(),
            failing=QualityDimension.CONTINUITY,
        ),
        policy=policy,
        evaluated_at=NOW.isoformat(),
    )
    remediation = plan_targeted_remediation(bundle, policy=policy)
    for value in (policy, bundle, remediation):
        assert quality_artifact_from_bytes(quality_artifact_to_bytes(value)) == value

    decision, *_ = _decision()
    assert candidate_decision_from_bytes(candidate_decision_to_bytes(decision)) == decision

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
    for value in (destination, candidate, assessment):
        assert release_artifact_from_bytes(release_artifact_to_bytes(value)) == value


@pytest.mark.parametrize(
    ("loader", "payload", "error"),
    (
        (
            quality_artifact_from_bytes,
            b'{"artifact_version":"quality-policy/1.0","artifact_version":"quality-bundle/1.0"}',
            QualitySerializationError,
        ),
        (
            candidate_decision_from_bytes,
            b'{"artifact_version":"candidate-decision/1.0","artifact_version":"candidate-decision/1.0"}',
            SelectionSerializationError,
        ),
        (
            release_artifact_from_bytes,
            b'{"artifact_version":"release-candidate/1.0","artifact_version":"release-assessment/1.0"}',
            ReleaseSerializationError,
        ),
    ),
)
def test_duplicate_json_keys_fail_closed(loader, payload, error) -> None:
    with pytest.raises(error):
        loader(payload)


def test_public_w05_serializers_reject_direct_dataclass_schema_rebounds() -> None:
    policy = target_quality_policy()
    with pytest.raises(QualityContractError):
        quality_artifact_to_bytes(
            replace(policy, require_one_human_release_approval=1)
        )

    decision, *_ = _decision()
    with pytest.raises(SelectionContractError):
        candidate_decision_to_bytes(
            replace(decision, status=CandidateDecisionStatus.AUTO_SELECTED.value)
        )

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
    with pytest.raises(ReleaseContractError):
        release_artifact_to_bytes(
            replace(assessment, required_independent_humans=True)
        )
