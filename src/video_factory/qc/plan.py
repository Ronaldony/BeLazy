"""QC plan builder and deterministic judgment over injected measurements.

Plan-only: never runs ffprobe/ffmpeg or any process. Measurement method strings
are hints for a human or channel-side tool (same pattern as EncodeCommandPlan).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
import shlex
from typing import Mapping, Sequence

from video_factory.domain import ArtifactReference, ArtifactVersion, HashDigest, OpaqueId, RelativeArtifactPath

from .contracts import Comparison, Constraint, Finding, Measurement, QCReport, Severity


class QCPlanError(ValueError):
    """Raised when a QC plan or judgment cannot be formed without guessing."""


class JudgmentStatus(StrEnum):
    """Per-constraint outcome. Missing measurements are inconclusive, not fail."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class MeasurementMethod:
    """One non-executing measurement method hint."""

    method: str
    argv_hint: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Expectation:
    """One technical expectation injected by the caller (channel config / packet).

    Core never hard-codes expected numbers; callers supply them.
    """

    measurement_id: OpaqueId
    comparison: Comparison
    operands: tuple[Decimal | str | int | bool, ...]
    severity: Severity = Severity.ERROR
    constraint_id: OpaqueId | None = None
    unit: str | None = None
    measure_method: str | None = None
    measure_argv_hint: tuple[str, ...] = ()
    fallback_measure_methods: tuple[MeasurementMethod, ...] = ()
    applicable: bool = True
    not_applicable_reason: str | None = None


@dataclass(frozen=True, slots=True)
class PlannedCheck:
    """One planned constraint plus optional measurement-method hint strings."""

    constraint: Constraint
    measure_method: str | None
    measure_argv_hint: tuple[str, ...]
    measure_command_string: str
    fallback_measure_methods: tuple[MeasurementMethod, ...]
    applicable: bool
    not_applicable_reason: str | None


@dataclass(frozen=True, slots=True)
class QCPlan:
    """Structured QC plan: what to measure and how to compare (no execution)."""

    constraints: tuple[Constraint, ...]
    checks: tuple[PlannedCheck, ...]


@dataclass(frozen=True, slots=True)
class ConstraintJudgment:
    constraint_id: OpaqueId
    status: JudgmentStatus
    finding: Finding | None
    message: str


@dataclass(frozen=True, slots=True)
class QCJudgment:
    """Result of comparing injected measurements against a plan."""

    plan: QCPlan
    measurements: tuple[Measurement, ...]
    judgments: tuple[ConstraintJudgment, ...]
    report: QCReport
    overall: JudgmentStatus
    pass_count: int
    warn_count: int
    fail_count: int
    inconclusive_count: int
    not_applicable_count: int


# Generic measurement-method templates (tool names only; no channel values).
# Callers may override via Expectation.measure_* fields.
_DEFAULT_MEASURE_HINTS: Mapping[
    str, tuple[str, tuple[str, ...], tuple[MeasurementMethod, ...]]
] = {
    "duration_seconds": (
        "ffprobe",
        (
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            "{path}",
        ),
        (MeasurementMethod("opencv", ("read_frame_timestamps", "{path}")),),
    ),
    "width": (
        "ffprobe",
        (
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            "{path}",
        ),
        (MeasurementMethod("opencv", ("read_frame_width", "{path}")),),
    ),
    "height": (
        "ffprobe",
        (
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=height",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            "{path}",
        ),
        (MeasurementMethod("opencv", ("read_frame_height", "{path}")),),
    ),
    "fps": (
        "ffprobe",
        (
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=avg_frame_rate",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            "{path}",
        ),
        (MeasurementMethod("opencv", ("read_frame_rate", "{path}")),),
    ),
    "aspect_ratio": (
        "ffprobe",
        (
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            "{path}",
        ),
        (MeasurementMethod("opencv", ("read_frame_dimensions", "{path}")),),
    ),
    "has_audio": (
        "ffprobe",
        (
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            "{path}",
        ),
        (MeasurementMethod("ffmpeg", ("-i", "{path}", "-f", "null", "-")),),
    ),
    "black_frame_ratio": (
        "opencv",
        ("sample_frames", "mean_luma", "{path}"),
        (),
    ),
    "silence_duration_seconds": (
        "ffmpeg",
        ("-i", "{path}", "-af", "silencedetect", "-f", "null", "-"),
        (),
    ),
}


def _as_opaque(value: object, label: str) -> OpaqueId:
    if not isinstance(value, str) or not value.strip():
        raise QCPlanError(f"{label} must be a non-empty string")
    return OpaqueId(value.strip())


def _as_comparison(value: object) -> Comparison:
    if isinstance(value, Comparison):
        return value
    if isinstance(value, str):
        try:
            return Comparison(value.strip().lower())
        except ValueError as error:
            raise QCPlanError(f"unknown comparison: {value!r}") from error
    raise QCPlanError(f"comparison must be Comparison or str; got {type(value).__name__}")


def _as_severity(value: object) -> Severity:
    if isinstance(value, Severity):
        return value
    if isinstance(value, str):
        try:
            return Severity(value.strip().lower())
        except ValueError as error:
            raise QCPlanError(f"unknown severity: {value!r}") from error
    raise QCPlanError(f"severity must be Severity or str; got {type(value).__name__}")


def _as_operands(raw: object) -> tuple[Decimal | str | int | bool, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise QCPlanError("operands must be a non-empty sequence")
    if not raw:
        raise QCPlanError("operands must be a non-empty sequence")
    out: list[Decimal | str | int | bool] = []
    for item in raw:
        if isinstance(item, bool):
            out.append(item)
        elif isinstance(item, int):
            out.append(item)
        elif isinstance(item, float):
            out.append(Decimal(str(item)))
        elif isinstance(item, Decimal):
            out.append(item)
        elif isinstance(item, str):
            out.append(item)
        else:
            raise QCPlanError(f"unsupported operand type: {type(item).__name__}")
    return tuple(out)


def expectation_from_mapping(data: Mapping[str, object]) -> Expectation:
    """Parse one expectation mapping (injected data; no defaults for values)."""

    if "measurement_id" not in data:
        raise QCPlanError("expectation requires measurement_id")
    if "comparison" not in data:
        raise QCPlanError("expectation requires comparison")
    if "operands" not in data:
        raise QCPlanError("expectation requires operands")

    measurement_id = _as_opaque(data["measurement_id"], "measurement_id")
    comparison = _as_comparison(data["comparison"])
    operands = _as_operands(data["operands"])
    severity = (
        _as_severity(data["severity"]) if "severity" in data else Severity.ERROR
    )
    constraint_id = (
        _as_opaque(data["constraint_id"], "constraint_id")
        if "constraint_id" in data and data["constraint_id"] is not None
        else None
    )
    unit = data.get("unit")
    if unit is not None and not isinstance(unit, str):
        raise QCPlanError("unit must be a string or null")
    measure_method = data.get("measure_method")
    if measure_method is not None and not isinstance(measure_method, str):
        raise QCPlanError("measure_method must be a string or null")
    argv_raw = data.get("measure_argv_hint", ())
    if argv_raw is None:
        argv: tuple[str, ...] = ()
    elif not isinstance(argv_raw, Sequence) or isinstance(argv_raw, (str, bytes, bytearray)):
        raise QCPlanError("measure_argv_hint must be a sequence of strings")
    else:
        argv = tuple(str(item) for item in argv_raw)
    fallback_raw = data.get("fallback_measure_methods", ())
    fallback: list[MeasurementMethod] = []
    if not isinstance(fallback_raw, Sequence) or isinstance(
        fallback_raw, (str, bytes, bytearray)
    ):
        raise QCPlanError("fallback_measure_methods must be an array")
    for index, item in enumerate(fallback_raw):
        if not isinstance(item, Mapping):
            raise QCPlanError(
                f"fallback_measure_methods[{index}] must be an object"
            )
        method_value = item.get("method")
        if not isinstance(method_value, str) or not method_value:
            raise QCPlanError(
                f"fallback_measure_methods[{index}].method must be non-empty"
            )
        fallback_argv = item.get("argv_hint", ())
        if not isinstance(fallback_argv, Sequence) or isinstance(
            fallback_argv, (str, bytes, bytearray)
        ):
            raise QCPlanError(
                f"fallback_measure_methods[{index}].argv_hint must be an array"
            )
        fallback.append(
            MeasurementMethod(
                method=method_value,
                argv_hint=tuple(str(part) for part in fallback_argv),
            )
        )
    applicable = data.get("applicable", True)
    if not isinstance(applicable, bool):
        raise QCPlanError("applicable must be boolean")
    reason = data.get("not_applicable_reason")
    if reason is not None and (not isinstance(reason, str) or not reason.strip()):
        raise QCPlanError("not_applicable_reason must be a non-empty string or null")
    if not applicable and reason is None:
        raise QCPlanError(
            "not_applicable_reason is required when applicable is false"
        )
    return Expectation(
        measurement_id=measurement_id,
        comparison=comparison,
        operands=operands,
        severity=severity,
        constraint_id=constraint_id,
        unit=unit,
        measure_method=measure_method,
        measure_argv_hint=argv,
        fallback_measure_methods=tuple(fallback),
        applicable=applicable,
        not_applicable_reason=reason,
    )


def _command_string(method: str | None, argv: Sequence[str]) -> str:
    if method is None:
        return ""
    parts = [method, *argv]
    return " ".join(shlex.quote(part) for part in parts)


def _resolve_hint(
    measurement_id: OpaqueId,
    method: str | None,
    argv: Sequence[str],
) -> tuple[
    str | None,
    tuple[str, ...],
    str,
    tuple[MeasurementMethod, ...],
]:
    if method is not None:
        argv_t = tuple(argv)
        return method, argv_t, _command_string(method, argv_t), ()
    default = _DEFAULT_MEASURE_HINTS.get(str(measurement_id))
    if default is None:
        return None, tuple(argv), "", ()
    d_method, d_argv, fallbacks = default
    return d_method, d_argv, _command_string(d_method, d_argv), fallbacks


def _expectation_to_constraint(item: Expectation, index: int) -> Constraint:
    constraint_id = item.constraint_id or OpaqueId(f"c-{item.measurement_id}-{index}")
    if not item.operands:
        raise QCPlanError(f"expectation {constraint_id!r} has empty operands")
    if item.comparison is Comparison.RANGE and len(item.operands) != 2:
        raise QCPlanError(
            f"expectation {constraint_id!r} comparison 'range' requires exactly two operands"
        )
    if item.comparison is Comparison.MEMBER_OF and len(item.operands) < 1:
        raise QCPlanError(
            f"expectation {constraint_id!r} comparison 'member_of' requires operands"
        )
    return Constraint(
        constraint_id=constraint_id,
        measurement_id=item.measurement_id,
        comparison=item.comparison,
        operands=item.operands,
        severity=item.severity,
    )


def build_qc_plan(
    constraints: Sequence[Constraint] = (),
    expectations: Sequence[Expectation] | Sequence[Mapping[str, object]] = (),
) -> QCPlan:
    """Build a QC plan from injected constraints and/or expectations.

    Parameters
    ----------
    constraints:
        Already-formed ``Constraint`` objects (policy-injected).
    expectations:
        Expectation objects or mappings converted into constraints. Expected
        numeric/string values come only from this injection — never from core
        constants.
    """

    planned: list[PlannedCheck] = []
    seen_ids: set[str] = set()

    for index, constraint in enumerate(constraints):
        cid = str(constraint.constraint_id)
        if cid in seen_ids:
            raise QCPlanError(f"duplicate constraint_id: {cid}")
        seen_ids.add(cid)
        method, argv, command, fallbacks = _resolve_hint(
            constraint.measurement_id, None, ()
        )
        planned.append(
            PlannedCheck(
                constraint=constraint,
                measure_method=method,
                measure_argv_hint=argv,
                measure_command_string=command,
                fallback_measure_methods=fallbacks,
                applicable=True,
                not_applicable_reason=None,
            )
        )

    for index, raw in enumerate(expectations):
        if isinstance(raw, Expectation):
            exp = raw
        elif isinstance(raw, Mapping):
            exp = expectation_from_mapping(raw)
        else:
            raise QCPlanError(
                f"expectations[{index}] must be Expectation or mapping; "
                f"got {type(raw).__name__}"
            )
        constraint = _expectation_to_constraint(exp, index)
        cid = str(constraint.constraint_id)
        if cid in seen_ids:
            raise QCPlanError(f"duplicate constraint_id: {cid}")
        seen_ids.add(cid)
        method, argv, command, default_fallbacks = _resolve_hint(
            constraint.measurement_id,
            exp.measure_method,
            exp.measure_argv_hint,
        )
        planned.append(
            PlannedCheck(
                constraint=constraint,
                measure_method=method,
                measure_argv_hint=argv,
                measure_command_string=command,
                fallback_measure_methods=(
                    exp.fallback_measure_methods or default_fallbacks
                ),
                applicable=exp.applicable,
                not_applicable_reason=exp.not_applicable_reason,
            )
        )

    if not planned:
        raise QCPlanError("build_qc_plan requires at least one constraint or expectation")

    planned_t = tuple(planned)
    return QCPlan(
        constraints=tuple(item.constraint for item in planned_t),
        checks=planned_t,
    )


def _to_decimal(value: Decimal | str | int | float | bool) -> Decimal:
    if isinstance(value, bool):
        raise QCPlanError("boolean is not a numeric measurement for comparison")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value)
        except Exception as error:  # noqa: BLE001
            raise QCPlanError(f"cannot parse numeric value from {value!r}") from error
    raise QCPlanError(f"unsupported numeric type: {type(value).__name__}")


def _compare(
    observed: Decimal | str | int | bool,
    comparison: Comparison,
    operands: tuple[Decimal | str | int | bool, ...],
) -> bool:
    if comparison is Comparison.EQUAL:
        if len(operands) != 1:
            raise QCPlanError("equal comparison requires exactly one operand")
        op = operands[0]
        if isinstance(observed, bool) or isinstance(op, bool):
            return observed == op
        if isinstance(observed, str) or isinstance(op, str):
            return str(observed) == str(op)
        return _to_decimal(observed) == _to_decimal(op)  # type: ignore[arg-type]

    if comparison is Comparison.MINIMUM:
        if len(operands) != 1:
            raise QCPlanError("minimum comparison requires exactly one operand")
        return _to_decimal(observed) >= _to_decimal(operands[0])  # type: ignore[arg-type]

    if comparison is Comparison.MAXIMUM:
        if len(operands) != 1:
            raise QCPlanError("maximum comparison requires exactly one operand")
        return _to_decimal(observed) <= _to_decimal(operands[0])  # type: ignore[arg-type]

    if comparison is Comparison.RANGE:
        if len(operands) != 2:
            raise QCPlanError("range comparison requires exactly two operands")
        lo = _to_decimal(operands[0])
        hi = _to_decimal(operands[1])
        value = _to_decimal(observed)  # type: ignore[arg-type]
        return lo <= value <= hi

    if comparison is Comparison.MEMBER_OF:
        if isinstance(observed, bool):
            return any(observed == op for op in operands)
        if isinstance(observed, str):
            return any(str(op) == observed for op in operands)
        obs_d = _to_decimal(observed)  # type: ignore[arg-type]
        for op in operands:
            if isinstance(op, str):
                if str(obs_d) == op:
                    return True
            elif not isinstance(op, bool) and _to_decimal(op) == obs_d:
                return True
        return False

    raise QCPlanError(f"unknown comparison: {comparison!r}")


def _measurement_index(
    measurements: Sequence[Measurement],
) -> dict[str, Measurement]:
    index: dict[str, Measurement] = {}
    for item in measurements:
        key = str(item.measurement_id)
        # Later values win (deterministic: last in caller order).
        index[key] = item
    return index


def _placeholder_artifact() -> ArtifactReference:
    return ArtifactReference(
        path=RelativeArtifactPath("unspecified"),
        sha256=HashDigest("0" * 64),
        artifact_version=ArtifactVersion("shot-qc/1.0"),
    )


def judge_measurements(
    plan: QCPlan,
    measurements: Sequence[Measurement],
    *,
    artifact: ArtifactReference | None = None,
) -> QCJudgment:
    """Compare injected measurements to the plan (pure / deterministic).

    Missing measurements yield ``inconclusive`` — never treated as automatic fail
    (same principle as analytics ``MissingMetricPolicy.inconclusive``).
    """

    if not plan.constraints:
        raise QCPlanError("plan has no constraints")

    index = _measurement_index(measurements)
    judgments: list[ConstraintJudgment] = []
    findings: list[Finding] = []
    measured: list[Measurement] = []

    for planned in plan.checks:
        constraint = planned.constraint
        mid = str(constraint.measurement_id)
        if not planned.applicable:
            message = (
                "not_applicable: "
                + str(planned.not_applicable_reason or "caller marked not applicable")
            )
            judgments.append(
                ConstraintJudgment(
                    constraint_id=constraint.constraint_id,
                    status=JudgmentStatus.NOT_APPLICABLE,
                    finding=None,
                    message=message,
                )
            )
            continue
        observed = index.get(mid)
        if observed is None:
            message = (
                f"inconclusive: measurement {mid!r} not provided; "
                f"constraint={constraint.constraint_id!r} "
                f"comparison={constraint.comparison.value}"
            )
            judgments.append(
                ConstraintJudgment(
                    constraint_id=constraint.constraint_id,
                    status=JudgmentStatus.INCONCLUSIVE,
                    finding=None,
                    message=message,
                )
            )
            continue

        measured.append(observed)
        try:
            passed = _compare(observed.value, constraint.comparison, constraint.operands)
        except QCPlanError as error:
            message = f"inconclusive: {error}"
            judgments.append(
                ConstraintJudgment(
                    constraint_id=constraint.constraint_id,
                    status=JudgmentStatus.INCONCLUSIVE,
                    finding=None,
                    message=message,
                )
            )
            continue

        if passed:
            status = JudgmentStatus.PASS
        elif constraint.severity is Severity.ERROR:
            status = JudgmentStatus.FAIL
        elif constraint.severity is Severity.WARNING:
            status = JudgmentStatus.WARN
        else:
            status = JudgmentStatus.PASS
        if not passed and constraint.severity is Severity.INFO:
            message = (
                "pass: informational finding; constraint_satisfied=False; "
                "severity=info does not lower judgment; "
                f"observed={observed.value!r} "
                f"comparison={constraint.comparison.value} "
                f"operands={constraint.operands!r} "
                f"measurement_id={mid!r}"
            )
        else:
            message = (
                f"{status.value}: observed={observed.value!r} "
                f"comparison={constraint.comparison.value} "
                f"operands={constraint.operands!r} "
                f"measurement_id={mid!r}"
            )
        finding = Finding(
            constraint_id=constraint.constraint_id,
            passed=passed,
            observed=observed,
            message=message,
        )
        findings.append(finding)
        judgments.append(
            ConstraintJudgment(
                constraint_id=constraint.constraint_id,
                status=status,
                finding=finding,
                message=message,
            )
        )

    pass_count = sum(1 for j in judgments if j.status is JudgmentStatus.PASS)
    warn_count = sum(1 for j in judgments if j.status is JudgmentStatus.WARN)
    fail_count = sum(1 for j in judgments if j.status is JudgmentStatus.FAIL)
    inconclusive_count = sum(
        1 for j in judgments if j.status is JudgmentStatus.INCONCLUSIVE
    )
    not_applicable_count = sum(
        1 for j in judgments if j.status is JudgmentStatus.NOT_APPLICABLE
    )
    if fail_count:
        overall = JudgmentStatus.FAIL
    elif inconclusive_count:
        overall = JudgmentStatus.INCONCLUSIVE
    elif warn_count:
        overall = JudgmentStatus.WARN
    else:
        overall = JudgmentStatus.PASS

    subject = artifact if artifact is not None else _placeholder_artifact()
    report = QCReport(
        artifact=subject,
        constraints=plan.constraints,
        measurements=tuple(measured),
        findings=tuple(findings),
    )
    return QCJudgment(
        plan=plan,
        measurements=tuple(measurements),
        judgments=tuple(judgments),
        report=report,
        overall=overall,
        pass_count=pass_count,
        warn_count=warn_count,
        fail_count=fail_count,
        inconclusive_count=inconclusive_count,
        not_applicable_count=not_applicable_count,
    )


def qc_plan_to_mapping(plan: QCPlan) -> dict[str, object]:
    """Serialize a QCPlan to a plain mapping (caller may persist)."""

    checks: list[dict[str, object]] = []
    for item in plan.checks:
        c = item.constraint
        checks.append(
            {
                "constraint_id": str(c.constraint_id),
                "measurement_id": str(c.measurement_id),
                "comparison": c.comparison.value,
                "operands": [
                    float(op) if isinstance(op, Decimal) else op for op in c.operands
                ],
                "severity": c.severity.value,
                "measure_method": item.measure_method,
                "measure_argv_hint": list(item.measure_argv_hint),
                "measure_command_string": item.measure_command_string,
                "fallback_measure_methods": [
                    {
                        "method": fallback.method,
                        "argv_hint": list(fallback.argv_hint),
                    }
                    for fallback in item.fallback_measure_methods
                ],
                "applicable": item.applicable,
                "not_applicable_reason": item.not_applicable_reason,
            }
        )
    return {
        "plan_kind": "qc-plan",
        "plan_version": "1.0",
        "checks": checks,
        "constraint_count": len(plan.constraints),
    }


def judgment_to_mapping(judgment: QCJudgment) -> dict[str, object]:
    """Serialize a QCJudgment (includes plan + verdict counts)."""

    rows: list[dict[str, object]] = []
    for item in judgment.judgments:
        row: dict[str, object] = {
            "constraint_id": str(item.constraint_id),
            "status": item.status.value,
            "message": item.message,
        }
        if item.finding is not None:
            row["passed"] = item.finding.passed
            row["observed"] = {
                "measurement_id": str(item.finding.observed.measurement_id),
                "value": (
                    float(item.finding.observed.value)
                    if isinstance(item.finding.observed.value, Decimal)
                    else item.finding.observed.value
                ),
                "unit": item.finding.observed.unit,
            }
        rows.append(row)
    return {
        "plan": qc_plan_to_mapping(judgment.plan),
        "overall": judgment.overall.value,
        "counts": {
            "pass": judgment.pass_count,
            "warn": judgment.warn_count,
            "fail": judgment.fail_count,
            "inconclusive": judgment.inconclusive_count,
            "not_applicable": judgment.not_applicable_count,
        },
        "judgments": rows,
        "artifact": {
            "path": str(judgment.report.artifact.path),
            "sha256": str(judgment.report.artifact.sha256),
            "artifact_version": str(judgment.report.artifact.artifact_version),
        },
    }
