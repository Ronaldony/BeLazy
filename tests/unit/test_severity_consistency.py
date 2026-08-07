"""Shared severity semantics across single-shot and continuity QC."""

from __future__ import annotations

import pytest

from video_factory.continuity import (
    ContinuityAxis,
    ContinuityExpectation,
    ContinuitySubject,
    build_continuity_qc_plan,
    judge_continuity,
)
from video_factory.domain import ArtifactReference, HashDigest, OpaqueId
from video_factory.qc import (
    Comparison,
    Expectation,
    JudgmentStatus,
    Measurement,
    Severity,
    build_qc_plan,
    judge_measurements,
)


def _qc_status(severity: Severity) -> JudgmentStatus:
    plan = build_qc_plan(
        expectations=[
            Expectation(
                measurement_id=OpaqueId("observed-state"),
                comparison=Comparison.EQUAL,
                operands=("expected",),
                severity=severity,
                constraint_id=OpaqueId("state-equality"),
            )
        ]
    )
    return judge_measurements(
        plan,
        [Measurement(OpaqueId("observed-state"), "different", None)],
    ).overall


def _continuity_status(severity: Severity) -> JudgmentStatus:
    subjects = (
        ContinuitySubject(
            shot_id=OpaqueId("shot-a"),
            artifact=ArtifactReference(
                path="generated/shot-a.mp4",
                sha256=HashDigest("a" * 64),
                artifact_version="media/1.0",
            ),
        ),
        ContinuitySubject(
            shot_id=OpaqueId("shot-b"),
            artifact=ArtifactReference(
                path="generated/shot-b.mp4",
                sha256=HashDigest("b" * 64),
                artifact_version="media/1.0",
            ),
        ),
    )
    plan = build_continuity_qc_plan(
        subjects,
        [
            ContinuityExpectation(
                element_id=OpaqueId("tracked-element"),
                axis=ContinuityAxis.ORIENTATION_SHAPE,
                from_shot_id=OpaqueId("shot-a"),
                to_shot_id=OpaqueId("shot-b"),
                severity=severity,
            )
        ],
    )
    comparison = plan.comparisons[0]
    measurements = (
        Measurement(comparison.from_observation_id, "shape-a", None),
        Measurement(comparison.to_observation_id, "shape-b", None),
    )
    return judge_continuity(plan, measurements).overall


@pytest.mark.parametrize(
    ("severity", "expected"),
    [
        (Severity.INFO, JudgmentStatus.PASS),
        (Severity.WARNING, JudgmentStatus.WARN),
        (Severity.ERROR, JudgmentStatus.FAIL),
    ],
)
def test_qc_and_continuity_use_the_same_severity_status(
    severity: Severity,
    expected: JudgmentStatus,
) -> None:
    assert _qc_status(severity) is expected
    assert _continuity_status(severity) is expected
