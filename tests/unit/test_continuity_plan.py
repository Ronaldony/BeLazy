"""Cross-shot continuity planning and judgment over generated clips.

A single-shot QC report cannot say "this element changed between shots".
These tests pin the relation the core adds, and pin that the core never
supplies a tolerance of its own.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from video_factory.artifacts import validate_artifact
from video_factory.continuity import (
    ContinuityAxis,
    ContinuityExpectation,
    ContinuityPlanError,
    ContinuitySubject,
    SamplePoint,
    build_continuity_qc_plan,
    continuity_plan_from_mapping,
    continuity_plan_to_mapping,
    continuity_qc_to_mapping,
    judge_continuity,
    rejudge_continuity_qc,
)
from video_factory.domain import ArtifactReference, HashDigest, OpaqueId
from video_factory.qc import (
    JudgmentStatus,
    Measurement,
    MeasurementMethod,
    Severity,
)


def _subject(shot_id: str, digest: str) -> ContinuitySubject:
    return ContinuitySubject(
        shot_id=OpaqueId(shot_id),
        artifact=ArtifactReference(
            path="06_generated/" + shot_id + ".mp4",
            sha256=HashDigest(digest * 64),
            artifact_version="media/1.0",
        ),
    )


def _subjects() -> tuple[ContinuitySubject, ...]:
    return (_subject("shot-1", "a"), _subject("shot-2", "b"))


def _expectation(
    axis: ContinuityAxis,
    *,
    tolerance: Decimal | None = None,
    severity: Severity = Severity.ERROR,
) -> ContinuityExpectation:
    return ContinuityExpectation(
        element_id=OpaqueId("element-1"),
        axis=axis,
        from_shot_id=OpaqueId("shot-1"),
        to_shot_id=OpaqueId("shot-2"),
        tolerance=tolerance,
        severity=severity,
    )


def _plan(axis: ContinuityAxis, **kwargs):
    return build_continuity_qc_plan(_subjects(), [_expectation(axis, **kwargs)])


def _measure(plan, left: object, right: object) -> tuple[Measurement, ...]:
    comparison = plan.comparisons[0]
    return (
        Measurement(comparison.from_observation_id, left, None),
        Measurement(comparison.to_observation_id, right, None),
    )


def test_plan_pairs_two_shots_into_one_comparison() -> None:
    plan = _plan(ContinuityAxis.RELATIVE_SCALE, tolerance=Decimal("0.15"))
    assert len(plan.comparisons) == 1
    assert len(plan.observations) == 2
    shots = {str(item.shot_id) for item in plan.observations}
    assert shots == {"shot-1", "shot-2"}
    samples = {item.sample_point for item in plan.observations}
    assert samples == {SamplePoint.LAST, SamplePoint.FIRST}


def test_numeric_axis_requires_caller_tolerance() -> None:
    """Core must never invent an allowed drift."""

    with pytest.raises(ContinuityPlanError, match="requires a caller-supplied"):
        _plan(ContinuityAxis.RELATIVE_SCALE)


def test_unknown_shot_is_rejected() -> None:
    expectation = ContinuityExpectation(
        element_id=OpaqueId("element-1"),
        axis=ContinuityAxis.PRESENCE,
        from_shot_id=OpaqueId("shot-1"),
        to_shot_id=OpaqueId("shot-404"),
    )
    with pytest.raises(ContinuityPlanError, match="not a declared subject"):
        build_continuity_qc_plan(_subjects(), [expectation])


def test_scale_drift_within_tolerance_passes() -> None:
    plan = _plan(ContinuityAxis.RELATIVE_SCALE, tolerance=Decimal("0.15"))
    judgment = judge_continuity(plan, _measure(plan, Decimal("1.0"), Decimal("1.1")))
    assert judgment.overall is JudgmentStatus.PASS


def test_scale_drift_beyond_tolerance_fails() -> None:
    """The EP001 defect: an element doubles in size between shots."""

    plan = _plan(ContinuityAxis.RELATIVE_SCALE, tolerance=Decimal("0.15"))
    judgment = judge_continuity(plan, _measure(plan, Decimal("1.0"), Decimal("2.0")))
    assert judgment.overall is JudgmentStatus.FAIL
    assert judgment.fail_count == 1
    assert "exceeds tolerance" in judgment.judgments[0].message


def test_warning_severity_downgrades_a_violation() -> None:
    plan = _plan(
        ContinuityAxis.RELATIVE_SCALE,
        tolerance=Decimal("0.15"),
        severity=Severity.WARNING,
    )
    judgment = judge_continuity(plan, _measure(plan, Decimal("1.0"), Decimal("2.0")))
    assert judgment.overall is JudgmentStatus.WARN


def test_info_severity_keeps_existing_pass_behavior() -> None:
    plan = _plan(
        ContinuityAxis.RELATIVE_SCALE,
        tolerance=Decimal("0.15"),
        severity=Severity.INFO,
    )
    judgment = judge_continuity(
        plan,
        _measure(plan, Decimal("1.0"), Decimal("2.0")),
    )
    assert judgment.overall is JudgmentStatus.PASS
    assert judgment.pass_count == 1
    assert judgment.warn_count == 0
    assert judgment.fail_count == 0


def test_shape_change_fails() -> None:
    plan = _plan(ContinuityAxis.ORIENTATION_SHAPE)
    judgment = judge_continuity(plan, _measure(plan, "open-left", "closed-ring"))
    assert judgment.overall is JudgmentStatus.FAIL
    assert "changes shape" in judgment.judgments[0].message


def test_shape_unchanged_passes() -> None:
    plan = _plan(ContinuityAxis.ORIENTATION_SHAPE)
    judgment = judge_continuity(plan, _measure(plan, "open-left", "open-left"))
    assert judgment.overall is JudgmentStatus.PASS


def test_disappearance_fails_but_appearance_does_not() -> None:
    plan = _plan(ContinuityAxis.PRESENCE)
    gone = judge_continuity(plan, _measure(plan, True, False))
    assert gone.overall is JudgmentStatus.FAIL
    assert "disappears" in gone.judgments[0].message

    appears = judge_continuity(plan, _measure(plan, False, True))
    assert appears.overall is JudgmentStatus.PASS


def test_missing_observation_is_inconclusive_not_fail() -> None:
    plan = _plan(ContinuityAxis.PRESENCE)
    judgment = judge_continuity(plan, ())
    assert judgment.overall is JudgmentStatus.INCONCLUSIVE
    assert judgment.fail_count == 0


def test_non_numeric_scale_observation_is_inconclusive() -> None:
    plan = _plan(ContinuityAxis.RELATIVE_SCALE, tolerance=Decimal("0.15"))
    judgment = judge_continuity(plan, _measure(plan, "wide", "narrow"))
    assert judgment.overall is JudgmentStatus.INCONCLUSIVE


def test_zero_baseline_is_inconclusive_not_division_error() -> None:
    plan = _plan(ContinuityAxis.RELATIVE_SCALE, tolerance=Decimal("0.15"))
    judgment = judge_continuity(plan, _measure(plan, Decimal("0"), Decimal("1")))
    assert judgment.overall is JudgmentStatus.INCONCLUSIVE


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (True, 1),
        (None, 1),
        ("NaN", "1"),
        ("Infinity", "1"),
        ("-Infinity", "1"),
        (float("nan"), 1),
        (float("inf"), 1),
        (1, 0),
        (1, -1),
    ],
)
def test_all_ineligible_scale_measurement_forms_are_inconclusive(
    left: object,
    right: object,
) -> None:
    plan = _plan(ContinuityAxis.RELATIVE_SCALE, tolerance=Decimal("0.15"))
    judgment = judge_continuity(plan, _measure(plan, left, right))
    assert judgment.overall is JudgmentStatus.INCONCLUSIVE


def test_finite_float_scale_measurements_are_numeric() -> None:
    plan = _plan(ContinuityAxis.RELATIVE_SCALE, tolerance=Decimal("0.15"))
    judgment = judge_continuity(plan, _measure(plan, 1.0, 1.1))
    assert judgment.overall is JudgmentStatus.PASS


@pytest.mark.parametrize(
    "tolerance",
    [
        Decimal("NaN"),
        Decimal("Infinity"),
        float("nan"),
        float("inf"),
        Decimal("-0.1"),
        True,
        "0.15",
    ],
)
def test_tolerance_must_be_finite_numeric_and_nonnegative(
    tolerance: object,
) -> None:
    with pytest.raises(ContinuityPlanError, match="tolerance"):
        _plan(ContinuityAxis.RELATIVE_SCALE, tolerance=tolerance)


def test_report_validates_against_the_registered_schema() -> None:
    plan = _plan(ContinuityAxis.RELATIVE_SCALE, tolerance=Decimal("0.15"))
    judgment = judge_continuity(plan, _measure(plan, Decimal("1.0"), Decimal("2.0")))
    document = continuity_qc_to_mapping(
        judgment,
        episode_id="ep-1",
        rules_version="rules-test",
        checked_at="2026-07-28T06:00:00Z",
    )
    result = validate_artifact(document)
    assert result.ok, result.error_texts


def test_serialized_report_rejudges_to_the_same_result() -> None:
    expectation = ContinuityExpectation(
        element_id=OpaqueId("element-1"),
        axis=ContinuityAxis.RELATIVE_SCALE,
        from_shot_id=OpaqueId("shot-1"),
        to_shot_id=OpaqueId("shot-2"),
        tolerance=Decimal("0.15"),
        severity=Severity.WARNING,
        measure_method="caller-measure",
        measure_argv_hint=("sample", "{path}"),
        fallback_measure_methods=(
            MeasurementMethod("caller-fallback", ("inspect", "{path}")),
        ),
    )
    plan = build_continuity_qc_plan(_subjects(), [expectation])
    judgment = judge_continuity(
        plan, _measure(plan, Decimal("1.0"), Decimal("2.0"))
    )
    document = continuity_qc_to_mapping(
        judgment,
        episode_id="ep-1",
        rules_version="rules-test",
        checked_at="2026-07-28T06:00:00Z",
    )

    assert document["measurements"][0] == {
        "measurement_id": str(plan.comparisons[0].from_observation_id),
        "value": "1.0",
        "unit": None,
    }
    reconstructed_plan = continuity_plan_from_mapping(document)
    assert reconstructed_plan == plan
    recomputed = rejudge_continuity_qc(document)
    assert recomputed.overall is judgment.overall
    assert [
        (item.comparison_id, item.status, item.message)
        for item in recomputed.judgments
    ] == [
        (item.comparison_id, item.status, item.message)
        for item in judgment.judgments
    ]


def test_structured_hash_ids_do_not_merge_dotted_opaque_ids() -> None:
    subjects = (
        _subject("a.b", "a"),
        _subject("a", "b"),
        _subject("to-1", "c"),
    )
    plan = build_continuity_qc_plan(
        subjects,
        [
            ContinuityExpectation(
                element_id=OpaqueId("c"),
                axis=ContinuityAxis.PRESENCE,
                from_shot_id=OpaqueId("a.b"),
                to_shot_id=OpaqueId("to-1"),
            ),
            ContinuityExpectation(
                element_id=OpaqueId("b.c"),
                axis=ContinuityAxis.PRESENCE,
                from_shot_id=OpaqueId("a"),
                to_shot_id=OpaqueId("to-1"),
            ),
        ],
    )
    from_ids = {
        str(comparison.from_observation_id) for comparison in plan.comparisons
    }
    assert len(from_ids) == 2
    assert len(plan.observations) == 4
    assert all(value.startswith("obs-") for value in from_ids)


def test_plan_mapping_is_data_only() -> None:
    plan = _plan(ContinuityAxis.PRESENCE)
    mapping = continuity_plan_to_mapping(plan)
    assert set(mapping) == {"subjects", "observations", "comparisons"}
    assert mapping["subjects"][0]["shot_id"] == "shot-1"
    assert mapping["subjects"][0]["artifact"]["artifact_version"] == "media/1.0"
    comparison = mapping["comparisons"][0]
    assert comparison["element_id"] == "element-1"
    assert comparison["severity"] == "error"
    assert "tolerance" not in comparison
