from copy import deepcopy

import pytest

from video_factory.artifacts import ArtifactSchemaRegistry, validate_artifact_mapping
from video_factory.quality import (
    QualityDimension,
    build_quality_bundle,
    plan_targeted_remediation,
    quality_bundle_to_mapping,
    quality_policy_to_mapping,
    remediation_plan_to_mapping,
    target_quality_policy,
)
from video_factory.release import (
    assess_release_candidate,
    destination_binding_to_mapping,
    release_assessment_to_mapping,
    release_candidate_to_mapping,
)
from video_factory.selection import candidate_decision_to_mapping
from tests.unit.test_authority_control import NOW
from tests.unit.test_candidate_decision import _decision
from tests.unit.test_quality_bundle import _context, _evaluations, _subject
from tests.unit.test_release_control import _authority, _release_fixture


def test_w05_schema_registry_and_all_public_mappings_are_strict() -> None:
    registry = ArtifactSchemaRegistry()
    assert len(registry.all_schemas()) == 80
    assert len(registry.list_versions()) == 75
    expected = {
        "quality-policy/1.0",
        "quality-bundle/1.0",
        "remediation-plan/1.0",
        "candidate-decision/1.0",
        "destination-binding/1.0",
        "release-candidate/1.0",
        "release-assessment/1.0",
    }
    assert expected <= set(registry.list_versions())

    policy = target_quality_policy()
    failed_bundle = build_quality_bundle(
        episode_id="episode-a",
        gate_context=_context(),
        evaluations=_evaluations(
            _subject(),
            failing=QualityDimension.CONTINUITY,
        ),
        policy=policy,
        evaluated_at=NOW.isoformat(),
    )
    remediation = plan_targeted_remediation(failed_bundle, policy=policy)
    candidate_decision, *_ = _decision()
    (
        release_candidate,
        release_candidate_ref,
        destination,
        release_verification,
    ) = _release_fixture()
    release_authority, release_ledger, release_authority_references = _authority(
        release_candidate,
        release_candidate_ref,
        destination,
        granted=False,
    )
    assessment = assess_release_candidate(
        release_candidate_ref=release_candidate_ref,
        release_candidate=release_candidate,
        release_candidate_verification=release_verification,
        destination=destination,
        current_context=release_candidate.gate_context,
        evaluated_at=NOW,
        authority=release_authority,
        authority_ledger=release_ledger,
        authority_references=release_authority_references,
    )
    mappings = (
        quality_policy_to_mapping(policy),
        quality_bundle_to_mapping(failed_bundle),
        remediation_plan_to_mapping(remediation),
        candidate_decision_to_mapping(candidate_decision),
        destination_binding_to_mapping(destination),
        release_candidate_to_mapping(release_candidate),
        release_assessment_to_mapping(assessment),
    )
    for mapping in mappings:
        result = validate_artifact_mapping(mapping, registry=registry)
        assert result.ok, result.error_texts
        tampered = deepcopy(mapping)
        tampered["unexpected"] = True
        assert not validate_artifact_mapping(tampered, registry=registry).ok


@pytest.mark.parametrize(
    "version",
    (
        "quality-policy/1.0",
        "quality-bundle/1.0",
        "remediation-plan/1.0",
        "candidate-decision/1.0",
        "destination-binding/1.0",
        "release-candidate/1.0",
        "release-assessment/1.0",
    ),
)
def test_w05_registered_versions_are_exact(version: str) -> None:
    assert ArtifactSchemaRegistry().get(version).artifact_version == version
