"""Policy-injected measurements and deterministic findings."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, Sequence

from video_factory.domain import ArtifactReference, OpaqueId


class Comparison(StrEnum):
    EQUAL = "equal"
    MINIMUM = "minimum"
    MAXIMUM = "maximum"
    RANGE = "range"
    MEMBER_OF = "member_of"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Constraint:
    constraint_id: OpaqueId
    measurement_id: OpaqueId
    comparison: Comparison
    operands: tuple[Decimal | str | int | bool, ...]
    severity: Severity


@dataclass(frozen=True, slots=True)
class Measurement:
    measurement_id: OpaqueId
    value: Decimal | float | str | int | bool | None
    unit: str | None


@dataclass(frozen=True, slots=True)
class Finding:
    constraint_id: OpaqueId
    passed: bool
    observed: Measurement
    message: str


@dataclass(frozen=True, slots=True)
class QCReport:
    artifact: ArtifactReference
    constraints: tuple[Constraint, ...]
    measurements: tuple[Measurement, ...]
    findings: tuple[Finding, ...]


class DeterministicCheck(Protocol):
    def evaluate(
        self,
        artifact: ArtifactReference,
        constraints: Sequence[Constraint],
    ) -> QCReport: ...

