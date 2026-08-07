"""Cross-shot continuity contracts for generated clips.

A single-shot QC report cannot express "this element changed between shots".
These contracts add the missing relation: two observations of the same opaque
element, taken from two different shot outputs, compared on one axis.

Core plans and judges only. It never extracts frames, never measures pixels,
and never supplies a tolerance of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from video_factory.domain import ArtifactReference, OpaqueId
from video_factory.qc import (
    JudgmentStatus,
    Measurement,
    MeasurementMethod,
    Severity,
)


class ContinuityAxis(StrEnum):
    """What is compared between two shots for one element."""

    #: Element size relative to a caller-defined reference (numeric).
    RELATIVE_SCALE = "relative_scale"
    #: Opaque orientation/silhouette token (string equality).
    ORIENTATION_SHAPE = "orientation_shape"
    #: Whether the element is present at all (boolean).
    PRESENCE = "presence"


class SamplePoint(StrEnum):
    """Which frame of a shot an observation is taken from."""

    FIRST = "first"
    MIDDLE = "middle"
    LAST = "last"


@dataclass(frozen=True, slots=True)
class ContinuitySubject:
    """One generated shot output participating in the comparison."""

    shot_id: OpaqueId
    artifact: ArtifactReference


@dataclass(frozen=True, slots=True)
class ObservationRequest:
    """One measurement the caller must take. Core does not take it."""

    observation_id: OpaqueId
    shot_id: OpaqueId
    element_id: OpaqueId
    axis: ContinuityAxis
    sample_point: SamplePoint
    measure_method: str | None = None
    measure_argv_hint: tuple[str, ...] = ()
    fallback_measure_methods: tuple[MeasurementMethod, ...] = ()


@dataclass(frozen=True, slots=True)
class ContinuityExpectation:
    """One caller-injected cross-shot expectation.

    Tolerances and severities are owner policy. Core holds no default numbers.
    """

    element_id: OpaqueId
    axis: ContinuityAxis
    from_shot_id: OpaqueId
    to_shot_id: OpaqueId
    from_sample: SamplePoint = SamplePoint.LAST
    to_sample: SamplePoint = SamplePoint.FIRST
    tolerance: Decimal | None = None
    severity: Severity = Severity.ERROR
    measure_method: str | None = None
    measure_argv_hint: tuple[str, ...] = ()
    fallback_measure_methods: tuple[MeasurementMethod, ...] = ()


@dataclass(frozen=True, slots=True)
class ContinuityComparison:
    """One planned comparison between two observations."""

    comparison_id: OpaqueId
    element_id: OpaqueId
    axis: ContinuityAxis
    from_observation_id: OpaqueId
    to_observation_id: OpaqueId
    tolerance: Decimal | None
    severity: Severity


@dataclass(frozen=True, slots=True)
class ContinuityQCPlan:
    """What to observe in which shot, and which pairs to compare."""

    subjects: tuple[ContinuitySubject, ...]
    observations: tuple[ObservationRequest, ...]
    comparisons: tuple[ContinuityComparison, ...]


@dataclass(frozen=True, slots=True)
class ContinuityComparisonJudgment:
    comparison_id: OpaqueId
    status: JudgmentStatus
    message: str
    observed_from: Measurement | None
    observed_to: Measurement | None


@dataclass(frozen=True, slots=True)
class ContinuityQCJudgment:
    plan: ContinuityQCPlan
    measurements: tuple[Measurement, ...]
    judgments: tuple[ContinuityComparisonJudgment, ...]
    overall: JudgmentStatus
    pass_count: int
    warn_count: int
    fail_count: int
    inconclusive_count: int
