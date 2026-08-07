"""QC plan builder and measurement judgment (synthetic fixtures only)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from video_factory.domain import OpaqueId
from video_factory.qc import (
    Comparison,
    Constraint,
    Expectation,
    JudgmentStatus,
    Measurement,
    MeasurementMethod,
    QCPlanError,
    Severity,
    build_qc_plan,
    judge_measurements,
    judgment_to_mapping,
    qc_plan_to_mapping,
)


def _expectation(
    measurement_id: str,
    comparison: Comparison,
    *operands: object,
    severity: Severity = Severity.ERROR,
) -> Expectation:
    return Expectation(
        measurement_id=OpaqueId(measurement_id),
        comparison=comparison,
        operands=tuple(operands),  # type: ignore[arg-type]
        severity=severity,
        constraint_id=OpaqueId(f"c-{measurement_id}"),
    )


def test_build_qc_plan_is_deterministic_for_same_expectations() -> None:
    expectations = [
        _expectation("duration_seconds", Comparison.RANGE, 30, 45),
        _expectation("fps", Comparison.MINIMUM, 24),
    ]
    plan_a = build_qc_plan(expectations=expectations)
    plan_b = build_qc_plan(expectations=expectations)
    assert plan_a.constraints == plan_b.constraints
    assert qc_plan_to_mapping(plan_a) == qc_plan_to_mapping(plan_b)
    assert len(plan_a.checks) == 2
    # Generic method hints only — no hard-coded channel target values in plan.
    duration_check = plan_a.checks[0]
    assert duration_check.measure_method == "ffprobe"
    assert "duration" in duration_check.measure_command_string


def test_build_qc_plan_from_injected_constraints_without_hardcoded_values() -> None:
    # Values come only from the injected constraint operands.
    constraints = [
        Constraint(
            constraint_id=OpaqueId("width-eq"),
            measurement_id=OpaqueId("width"),
            comparison=Comparison.EQUAL,
            operands=(1080,),
            severity=Severity.ERROR,
        )
    ]
    plan = build_qc_plan(constraints=constraints)
    assert plan.constraints[0].operands == (1080,)
    assert plan.checks[0].measure_method == "ffprobe"


def test_judge_pass_fail_inconclusive_paths() -> None:
    plan = build_qc_plan(
        expectations=[
            _expectation(
                "duration_seconds",
                Comparison.RANGE,
                Decimal("30"),
                Decimal("45"),
            ),
            _expectation("fps", Comparison.MINIMUM, 24),
            _expectation("width", Comparison.EQUAL, 1080),
        ]
    )
    # pass: duration in range, fps ok; width missing -> inconclusive
    judgment = judge_measurements(
        plan,
        [
            Measurement(OpaqueId("duration_seconds"), Decimal("40"), "s"),
            Measurement(OpaqueId("fps"), 30, "fps"),
        ],
    )
    assert judgment.pass_count == 2
    assert judgment.fail_count == 0
    assert judgment.inconclusive_count == 1
    assert judgment.overall is JudgmentStatus.INCONCLUSIVE
    by_id = {str(j.constraint_id): j for j in judgment.judgments}
    assert by_id["c-width"].status is JudgmentStatus.INCONCLUSIVE
    assert by_id["c-width"].finding is None

    # fail path
    failed = judge_measurements(
        plan,
        [
            Measurement(OpaqueId("duration_seconds"), Decimal("10"), "s"),
            Measurement(OpaqueId("fps"), 12, "fps"),
            Measurement(OpaqueId("width"), 720, "px"),
        ],
    )
    assert failed.fail_count == 3
    assert failed.overall is JudgmentStatus.FAIL

    # all pass
    passed = judge_measurements(
        plan,
        [
            Measurement(OpaqueId("duration_seconds"), Decimal("36"), "s"),
            Measurement(OpaqueId("fps"), 24, "fps"),
            Measurement(OpaqueId("width"), 1080, "px"),
        ],
    )
    assert passed.fail_count == 0
    assert passed.inconclusive_count == 0
    assert passed.overall is JudgmentStatus.PASS
    assert all(f.passed for f in passed.report.findings)


def test_empty_plan_rejected() -> None:
    with pytest.raises(QCPlanError, match="at least one"):
        build_qc_plan()


def test_warning_failure_is_warn_not_fail() -> None:
    plan = build_qc_plan(
        expectations=[
            _expectation(
                "black_frame_ratio",
                Comparison.MAXIMUM,
                Decimal("0.1"),
                severity=Severity.WARNING,
            )
        ]
    )
    judgment = judge_measurements(
        plan,
        [Measurement(OpaqueId("black_frame_ratio"), Decimal("0.5"), None)],
    )
    assert judgment.overall is JudgmentStatus.WARN
    assert judgment.warn_count == 1
    assert judgment.fail_count == 0


def test_info_failure_is_recorded_without_lowering_judgment() -> None:
    plan = build_qc_plan(
        expectations=[
            _expectation(
                "rules_version_observed",
                Comparison.EQUAL,
                "expected-version",
                severity=Severity.INFO,
            )
        ]
    )
    judgment = judge_measurements(
        plan,
        [
            Measurement(
                OpaqueId("rules_version_observed"),
                "different-version",
                None,
            )
        ],
    )

    assert judgment.overall is JudgmentStatus.PASS
    assert judgment.pass_count == 1
    assert judgment.warn_count == 0
    assert judgment.fail_count == 0
    item = judgment.judgments[0]
    assert item.status is JudgmentStatus.PASS
    assert item.finding is not None
    assert item.finding.passed is False
    assert "informational finding" in item.message
    assert "does not lower judgment" in item.message
    assert judgment_to_mapping(judgment)["counts"] == {
        "pass": 1,
        "warn": 0,
        "fail": 0,
        "inconclusive": 0,
        "not_applicable": 0,
    }


def test_boolean_operand_and_not_applicable_are_supported() -> None:
    plan = build_qc_plan(
        expectations=[
            Expectation(
                measurement_id=OpaqueId("has_audio"),
                comparison=Comparison.EQUAL,
                operands=(True,),
                constraint_id=OpaqueId("c-has-audio"),
            ),
            Expectation(
                measurement_id=OpaqueId("silence_duration_seconds"),
                comparison=Comparison.MAXIMUM,
                operands=(0,),
                constraint_id=OpaqueId("c-silence"),
                applicable=False,
                not_applicable_reason="episode has no soundtrack",
            ),
        ]
    )
    judgment = judge_measurements(
        plan,
        [Measurement(OpaqueId("has_audio"), True, None)],
    )
    assert judgment.pass_count == 1
    assert judgment.not_applicable_count == 1
    assert judgment.overall is JudgmentStatus.PASS


def test_fallback_measurement_method_is_preserved() -> None:
    plan = build_qc_plan(
        expectations=[
            Expectation(
                measurement_id=OpaqueId("custom"),
                comparison=Comparison.EQUAL,
                operands=(1,),
                fallback_measure_methods=(
                    MeasurementMethod("fallback-probe", ("--input", "{path}")),
                ),
            )
        ]
    )
    mapping = qc_plan_to_mapping(plan)
    assert mapping["checks"][0]["fallback_measure_methods"][0]["method"] == "fallback-probe"
