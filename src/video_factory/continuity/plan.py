"""Plan-only cross-shot continuity QC: what to observe, and how to compare."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
import math

from video_factory.artifacts import validate_artifact
from video_factory.config import canonical_sha256
from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    HashDigest,
    OpaqueId,
    RelativeArtifactPath,
)
from video_factory.qc import (
    JudgmentStatus,
    Measurement,
    MeasurementMethod,
    Severity,
)

from .contracts import (
    ContinuityAxis,
    ContinuityComparison,
    ContinuityComparisonJudgment,
    ContinuityExpectation,
    ContinuityQCJudgment,
    ContinuityQCPlan,
    ContinuitySubject,
    ObservationRequest,
    SamplePoint,
)

#: Axes whose comparison needs a caller-supplied numeric tolerance.
NUMERIC_AXES = frozenset({ContinuityAxis.RELATIVE_SCALE})


class ContinuityPlanError(ValueError):
    """Raised when a continuity plan cannot be formed without guessing."""


def _derived_id(prefix: str, payload: Mapping[str, object]) -> OpaqueId:
    """Hash a structured identity instead of joining ambiguous opaque ids."""

    return OpaqueId(f"{prefix}-{canonical_sha256(dict(payload))}")


def _observation_id(
    shot_id: OpaqueId,
    element_id: OpaqueId,
    axis: ContinuityAxis,
    sample: SamplePoint,
) -> OpaqueId:
    return _derived_id(
        "obs",
        {
            "shot_id": str(shot_id),
            "element_id": str(element_id),
            "axis": axis.value,
            "sample_point": sample.value,
        },
    )


def _comparison_id(expectation: ContinuityExpectation) -> OpaqueId:
    return _derived_id(
        "cmp",
        {
            "element_id": str(expectation.element_id),
            "axis": expectation.axis.value,
            "from_shot_id": str(expectation.from_shot_id),
            "from_sample": expectation.from_sample.value,
            "to_shot_id": str(expectation.to_shot_id),
            "to_sample": expectation.to_sample.value,
        },
    )


def _validated_tolerance(value: object, *, label: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, float)):
        raise ContinuityPlanError(f"{label} must be a finite numeric value")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ContinuityPlanError(
            f"{label} must be a finite numeric value"
        ) from None
    if not parsed.is_finite():
        raise ContinuityPlanError(f"{label} must be a finite numeric value")
    if parsed < 0:
        raise ContinuityPlanError(f"{label} must not be negative")
    return parsed


def build_continuity_qc_plan(
    subjects: Sequence[ContinuitySubject],
    expectations: Sequence[ContinuityExpectation],
) -> ContinuityQCPlan:
    """Turn injected cross-shot expectations into observations and comparisons.

    Core validates that every referenced shot is a declared subject and that
    numeric axes carry an owner-supplied tolerance. It performs no measurement.
    """

    if not subjects:
        raise ContinuityPlanError("at least one subject shot is required")
    if not expectations:
        raise ContinuityPlanError("at least one expectation is required")

    known: dict[str, ContinuitySubject] = {}
    for subject in subjects:
        key = str(subject.shot_id)
        if not key:
            raise ContinuityPlanError("subject shot_id must be non-empty")
        if key in known:
            raise ContinuityPlanError(f"duplicate subject shot_id: {key}")
        known[key] = subject

    observations: dict[str, ObservationRequest] = {}
    comparisons: list[ContinuityComparison] = []
    seen_comparisons: set[str] = set()

    for index, expectation in enumerate(expectations):
        for label, shot_id in (
            ("from_shot_id", expectation.from_shot_id),
            ("to_shot_id", expectation.to_shot_id),
        ):
            if str(shot_id) not in known:
                raise ContinuityPlanError(
                    f"expectations[{index}].{label} is not a declared subject: "
                    f"{shot_id}"
                )
        if str(expectation.from_shot_id) == str(expectation.to_shot_id) and (
            expectation.from_sample is expectation.to_sample
        ):
            raise ContinuityPlanError(
                f"expectations[{index}] compares an observation with itself"
            )
        if not str(expectation.element_id):
            raise ContinuityPlanError(
                f"expectations[{index}].element_id must be non-empty"
            )
        tolerance = _validated_tolerance(
            expectation.tolerance,
            label=f"expectations[{index}].tolerance",
        )
        if expectation.axis in NUMERIC_AXES and tolerance is None:
            raise ContinuityPlanError(
                f"expectations[{index}] on axis {expectation.axis.value} "
                "requires a caller-supplied tolerance; core holds no default"
            )

        pair: list[OpaqueId] = []
        for shot_id, sample in (
            (expectation.from_shot_id, expectation.from_sample),
            (expectation.to_shot_id, expectation.to_sample),
        ):
            observation_id = _observation_id(
                shot_id, expectation.element_id, expectation.axis, sample
            )
            existing = observations.get(str(observation_id))
            if existing is None:
                observations[str(observation_id)] = ObservationRequest(
                    observation_id=observation_id,
                    shot_id=shot_id,
                    element_id=expectation.element_id,
                    axis=expectation.axis,
                    sample_point=sample,
                    measure_method=expectation.measure_method,
                    measure_argv_hint=tuple(expectation.measure_argv_hint),
                    fallback_measure_methods=tuple(
                        expectation.fallback_measure_methods
                    ),
                )
            pair.append(observation_id)

        comparison_id = _comparison_id(expectation)
        if str(comparison_id) in seen_comparisons:
            raise ContinuityPlanError(
                f"duplicate comparison: {comparison_id}"
            )
        seen_comparisons.add(str(comparison_id))
        comparisons.append(
            ContinuityComparison(
                comparison_id=comparison_id,
                element_id=expectation.element_id,
                axis=expectation.axis,
                from_observation_id=pair[0],
                to_observation_id=pair[1],
                tolerance=tolerance,
                severity=expectation.severity,
            )
        )

    return ContinuityQCPlan(
        subjects=tuple(subjects),
        observations=tuple(observations.values()),
        comparisons=tuple(comparisons),
    )


def _numeric(value: object) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if not isinstance(value, (Decimal, int, float, str)):
        return None
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _violation_status(severity: Severity) -> JudgmentStatus:
    if severity is Severity.ERROR:
        return JudgmentStatus.FAIL
    if severity is Severity.WARNING:
        return JudgmentStatus.WARN
    return JudgmentStatus.PASS


def _judge_one(
    comparison: ContinuityComparison,
    left: Measurement | None,
    right: Measurement | None,
) -> ContinuityComparisonJudgment:
    def result(status: JudgmentStatus, message: str) -> ContinuityComparisonJudgment:
        return ContinuityComparisonJudgment(
            comparison_id=comparison.comparison_id,
            status=status,
            message=message,
            observed_from=left,
            observed_to=right,
        )

    if left is None or right is None:
        missing = []
        if left is None:
            missing.append(str(comparison.from_observation_id))
        if right is None:
            missing.append(str(comparison.to_observation_id))
        return result(
            JudgmentStatus.INCONCLUSIVE,
            "missing observation(s): " + ", ".join(missing),
        )

    if comparison.axis is ContinuityAxis.PRESENCE:
        if not isinstance(left.value, bool) or not isinstance(right.value, bool):
            return result(
                JudgmentStatus.INCONCLUSIVE,
                "presence observations must be boolean",
            )
        if left.value and not right.value:
            return result(
                _violation_status(comparison.severity),
                f"{comparison.element_id} disappears after the earlier shot",
            )
        return result(JudgmentStatus.PASS, "presence is continuous")

    if comparison.axis is ContinuityAxis.ORIENTATION_SHAPE:
        if not isinstance(left.value, str) or not isinstance(right.value, str):
            return result(
                JudgmentStatus.INCONCLUSIVE,
                "orientation/shape observations must be opaque strings",
            )
        if left.value != right.value:
            return result(
                _violation_status(comparison.severity),
                f"{comparison.element_id} changes shape: "
                f"{left.value!r} -> {right.value!r}",
            )
        return result(JudgmentStatus.PASS, "orientation/shape is unchanged")

    left_value = _numeric(left.value)
    right_value = _numeric(right.value)
    if left_value is None or right_value is None:
        return result(
            JudgmentStatus.INCONCLUSIVE,
            "relative scale observations must be numeric",
        )
    if left_value <= 0 or right_value <= 0:
        return result(
            JudgmentStatus.INCONCLUSIVE,
            "relative scale observations must both be greater than zero",
        )
    if comparison.tolerance is None:  # pragma: no cover - guarded when planning
        return result(
            JudgmentStatus.INCONCLUSIVE,
            "no tolerance was supplied for this comparison",
        )
    drift = abs(right_value / left_value - Decimal(1))
    if drift <= comparison.tolerance:
        return result(
            JudgmentStatus.PASS,
            f"relative scale drift {drift} within tolerance "
            f"{comparison.tolerance}",
        )
    return result(
        _violation_status(comparison.severity),
        f"{comparison.element_id} relative scale drift {drift} exceeds "
        f"tolerance {comparison.tolerance}",
    )


def judge_continuity(
    plan: ContinuityQCPlan,
    measurements: Sequence[Measurement],
) -> ContinuityQCJudgment:
    """Compare caller-supplied observations against a plan. No measuring here."""

    index: dict[str, Measurement] = {}
    for measurement in measurements:
        key = str(measurement.measurement_id)
        if key in index:
            raise ContinuityPlanError(f"duplicate measurement_id: {key}")
        index[key] = measurement

    judgments = tuple(
        _judge_one(
            comparison,
            index.get(str(comparison.from_observation_id)),
            index.get(str(comparison.to_observation_id)),
        )
        for comparison in plan.comparisons
    )

    pass_count = sum(1 for j in judgments if j.status is JudgmentStatus.PASS)
    warn_count = sum(1 for j in judgments if j.status is JudgmentStatus.WARN)
    fail_count = sum(1 for j in judgments if j.status is JudgmentStatus.FAIL)
    inconclusive_count = sum(
        1 for j in judgments if j.status is JudgmentStatus.INCONCLUSIVE
    )
    if fail_count:
        overall = JudgmentStatus.FAIL
    elif inconclusive_count:
        overall = JudgmentStatus.INCONCLUSIVE
    elif warn_count:
        overall = JudgmentStatus.WARN
    else:
        overall = JudgmentStatus.PASS

    return ContinuityQCJudgment(
        plan=plan,
        measurements=tuple(measurements),
        judgments=judgments,
        overall=overall,
        pass_count=pass_count,
        warn_count=warn_count,
        fail_count=fail_count,
        inconclusive_count=inconclusive_count,
    )


def continuity_qc_to_mapping(
    judgment: ContinuityQCJudgment,
    *,
    episode_id: str,
    rules_version: str,
    checked_at: str,
) -> dict[str, object]:
    """Serialize computed evidence; the caller supplies the audit timestamp."""

    plan_mapping = continuity_plan_to_mapping(judgment.plan)
    return {
        "artifact_version": "continuity-qc/1.0",
        "rules_version": rules_version,
        "episode_id": episode_id,
        "checked_at": checked_at,
        "subjects": plan_mapping["subjects"],
        "observations": plan_mapping["observations"],
        "comparisons": plan_mapping["comparisons"],
        "measurements": [
            _measurement_to_mapping(measurement)
            for measurement in judgment.measurements
        ],
        "judgments": [
            {
                "comparison_id": str(item.comparison_id),
                "status": item.status.value,
                "message": item.message,
            }
            for item in judgment.judgments
        ],
        "verdict": judgment.overall.value,
    }


def continuity_plan_to_mapping(plan: ContinuityQCPlan) -> dict[str, object]:
    """Expose the plan as data so a caller can drive its own measurement tool."""

    return {
        "subjects": [
            {
                "shot_id": str(subject.shot_id),
                "artifact": {
                    "path": str(subject.artifact.path),
                    "sha256": str(subject.artifact.sha256),
                    "artifact_version": str(subject.artifact.artifact_version),
                },
            }
            for subject in plan.subjects
        ],
        "observations": [
            {
                "observation_id": str(observation.observation_id),
                "shot_id": str(observation.shot_id),
                "element_id": str(observation.element_id),
                "axis": observation.axis.value,
                "sample_point": observation.sample_point.value,
                "measure_method": observation.measure_method,
                "measure_argv_hint": list(observation.measure_argv_hint),
                "fallback_measure_methods": [
                    {"method": item.method, "argv_hint": list(item.argv_hint)}
                    for item in observation.fallback_measure_methods
                ],
            }
            for observation in plan.observations
        ],
        "comparisons": [
            {
                "comparison_id": str(comparison.comparison_id),
                "axis": comparison.axis.value,
                "element_id": str(comparison.element_id),
                "from_observation_id": str(comparison.from_observation_id),
                "to_observation_id": str(comparison.to_observation_id),
                "severity": comparison.severity.value,
                **(
                    {"tolerance": _decimal_text(comparison.tolerance)}
                    if comparison.tolerance is not None
                    else {}
                ),
            }
            for comparison in plan.comparisons
        ],
    }


def _decimal_text(value: Decimal) -> str:
    if value.is_zero():
        return "0"
    return str(value)


def _measurement_to_mapping(measurement: Measurement) -> dict[str, object]:
    value = measurement.value
    if isinstance(value, Decimal):
        serialized: object = str(value)
    elif isinstance(value, float) and not math.isfinite(value):
        serialized = str(value)
    else:
        serialized = value
    return {
        "measurement_id": str(measurement.measurement_id),
        "value": serialized,
        "unit": measurement.unit,
    }


def _required_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ContinuityPlanError(f"{label} must be an object")
    return value


def _required_array(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(
        value, (str, bytes, bytearray)
    ):
        raise ContinuityPlanError(f"{label} must be an array")
    return value


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContinuityPlanError(f"{label} must be a non-empty string")
    return value


def continuity_plan_from_mapping(data: Mapping[str, object]) -> ContinuityQCPlan:
    """Reconstruct every plan input from a serialized plan or QC document."""

    try:
        subjects: list[ContinuitySubject] = []
        for index, raw_subject in enumerate(
            _required_array(data.get("subjects"), "subjects")
        ):
            subject = _required_mapping(raw_subject, f"subjects[{index}]")
            artifact = _required_mapping(
                subject.get("artifact"), f"subjects[{index}].artifact"
            )
            subjects.append(
                ContinuitySubject(
                    shot_id=OpaqueId(
                        _required_string(
                            subject.get("shot_id"), f"subjects[{index}].shot_id"
                        )
                    ),
                    artifact=ArtifactReference(
                        path=RelativeArtifactPath(
                            _required_string(
                                artifact.get("path"),
                                f"subjects[{index}].artifact.path",
                            )
                        ),
                        sha256=HashDigest(
                            _required_string(
                                artifact.get("sha256"),
                                f"subjects[{index}].artifact.sha256",
                            )
                        ),
                        artifact_version=ArtifactVersion(
                            _required_string(
                                artifact.get("artifact_version"),
                                f"subjects[{index}].artifact.artifact_version",
                            )
                        ),
                    ),
                )
            )

        observations: list[ObservationRequest] = []
        for index, raw_observation in enumerate(
            _required_array(data.get("observations"), "observations")
        ):
            observation = _required_mapping(
                raw_observation, f"observations[{index}]"
            )
            raw_fallbacks = _required_array(
                observation.get("fallback_measure_methods", ()),
                f"observations[{index}].fallback_measure_methods",
            )
            fallbacks: list[MeasurementMethod] = []
            for fallback_index, raw_fallback in enumerate(raw_fallbacks):
                fallback = _required_mapping(
                    raw_fallback,
                    f"observations[{index}].fallback_measure_methods"
                    f"[{fallback_index}]",
                )
                fallback_argv = _required_array(
                    fallback.get("argv_hint", ()),
                    f"observations[{index}].fallback_measure_methods"
                    f"[{fallback_index}].argv_hint",
                )
                fallbacks.append(
                    MeasurementMethod(
                        method=_required_string(
                            fallback.get("method"),
                            f"observations[{index}].fallback_measure_methods"
                            f"[{fallback_index}].method",
                        ),
                        argv_hint=tuple(str(item) for item in fallback_argv),
                    )
                )
            argv = _required_array(
                observation.get("measure_argv_hint", ()),
                f"observations[{index}].measure_argv_hint",
            )
            method = observation.get("measure_method")
            if method is not None and not isinstance(method, str):
                raise ContinuityPlanError(
                    f"observations[{index}].measure_method must be a string or null"
                )
            observations.append(
                ObservationRequest(
                    observation_id=OpaqueId(
                        _required_string(
                            observation.get("observation_id"),
                            f"observations[{index}].observation_id",
                        )
                    ),
                    shot_id=OpaqueId(
                        _required_string(
                            observation.get("shot_id"),
                            f"observations[{index}].shot_id",
                        )
                    ),
                    element_id=OpaqueId(
                        _required_string(
                            observation.get("element_id"),
                            f"observations[{index}].element_id",
                        )
                    ),
                    axis=ContinuityAxis(
                        _required_string(
                            observation.get("axis"),
                            f"observations[{index}].axis",
                        )
                    ),
                    sample_point=SamplePoint(
                        _required_string(
                            observation.get("sample_point"),
                            f"observations[{index}].sample_point",
                        )
                    ),
                    measure_method=method,
                    measure_argv_hint=tuple(str(item) for item in argv),
                    fallback_measure_methods=tuple(fallbacks),
                )
            )

        comparisons: list[ContinuityComparison] = []
        for index, raw_comparison in enumerate(
            _required_array(data.get("comparisons"), "comparisons")
        ):
            comparison = _required_mapping(
                raw_comparison, f"comparisons[{index}]"
            )
            tolerance_raw = comparison.get("tolerance")
            tolerance: Decimal | None = None
            if tolerance_raw is not None:
                try:
                    tolerance = Decimal(str(tolerance_raw))
                except (InvalidOperation, ValueError):
                    raise ContinuityPlanError(
                        f"comparisons[{index}].tolerance is not numeric"
                    ) from None
                tolerance = _validated_tolerance(
                    tolerance, label=f"comparisons[{index}].tolerance"
                )
            comparisons.append(
                ContinuityComparison(
                    comparison_id=OpaqueId(
                        _required_string(
                            comparison.get("comparison_id"),
                            f"comparisons[{index}].comparison_id",
                        )
                    ),
                    element_id=OpaqueId(
                        _required_string(
                            comparison.get("element_id"),
                            f"comparisons[{index}].element_id",
                        )
                    ),
                    axis=ContinuityAxis(
                        _required_string(
                            comparison.get("axis"),
                            f"comparisons[{index}].axis",
                        )
                    ),
                    from_observation_id=OpaqueId(
                        _required_string(
                            comparison.get("from_observation_id"),
                            f"comparisons[{index}].from_observation_id",
                        )
                    ),
                    to_observation_id=OpaqueId(
                        _required_string(
                            comparison.get("to_observation_id"),
                            f"comparisons[{index}].to_observation_id",
                        )
                    ),
                    tolerance=tolerance,
                    severity=Severity(
                        _required_string(
                            comparison.get("severity"),
                            f"comparisons[{index}].severity",
                        )
                    ),
                )
            )
    except ValueError as error:
        if isinstance(error, ContinuityPlanError):
            raise
        raise ContinuityPlanError(str(error)) from error

    return ContinuityQCPlan(
        subjects=tuple(subjects),
        observations=tuple(observations),
        comparisons=tuple(comparisons),
    )


def rejudge_continuity_qc(
    document: Mapping[str, object],
) -> ContinuityQCJudgment:
    """Recompute a QC verdict from the serialized document alone."""

    validation = validate_artifact(document)
    if not validation.ok:
        raise ContinuityPlanError(
            "continuity QC schema validation failed: "
            + "; ".join(validation.error_texts)
        )
    plan = continuity_plan_from_mapping(document)
    measurements: list[Measurement] = []
    for index, raw_measurement in enumerate(
        _required_array(document.get("measurements"), "measurements")
    ):
        measurement = _required_mapping(
            raw_measurement, f"measurements[{index}]"
        )
        unit = measurement.get("unit")
        if unit is not None and not isinstance(unit, str):
            raise ContinuityPlanError(
                f"measurements[{index}].unit must be a string or null"
            )
        measurements.append(
            Measurement(
                measurement_id=OpaqueId(
                    _required_string(
                        measurement.get("measurement_id"),
                        f"measurements[{index}].measurement_id",
                    )
                ),
                value=measurement.get("value"),
                unit=unit,
            )
        )
    return judge_continuity(plan, measurements)


__all__ = [
    "ContinuityPlanError",
    "NUMERIC_AXES",
    "build_continuity_qc_plan",
    "continuity_plan_from_mapping",
    "continuity_plan_to_mapping",
    "continuity_qc_to_mapping",
    "judge_continuity",
    "rejudge_continuity_qc",
]
