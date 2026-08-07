"""Analytics and retro contracts: injected metrics + policy data only.

The core never calls external analytics APIs. Callers collect metrics offline
and supply them as ``AnalyticsRecord`` documents. Hypothesis names, checkpoint
window labels, and metric keys are opaque channel data.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from video_factory.domain import ArtifactReference, OpaqueId, RoleId


class Comparator(StrEnum):
    """Closed comparison operators for verification rules."""

    GTE = "gte"
    LTE = "lte"
    GT = "gt"
    LT = "lt"
    EQ = "eq"
    BETWEEN = "between"


class MissingMetricPolicy(StrEnum):
    """How to treat a missing or null metric value.

    Only ``inconclusive`` is defined: absence must never be treated as
    refutation. Callers collect data; the core does not invent failures.
    """

    INCONCLUSIVE = "inconclusive"


class RuleVerdict(StrEnum):
    """Deterministic outcome of one verification rule."""

    SUPPORTED = "supported"
    REFUTED = "refuted"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True, slots=True)
class CheckpointWindow:
    """Opaque time-window identity and free-text definition (channel-supplied)."""

    window_id: OpaqueId
    definition: str


@dataclass(frozen=True, slots=True)
class AnalyticsRecord:
    """One post-publish metrics snapshot for an opaque episode target."""

    episode_id: OpaqueId
    collected_at: str
    checkpoint_window: CheckpointWindow
    metrics: Mapping[str, float | None]
    collector_role: RoleId
    publish_record_ref: ArtifactReference | None = None
    rules_version: str | None = None


@dataclass(frozen=True, slots=True)
class VerificationRule:
    """One hypothesis↔metric check; all identifiers are opaque channel data."""

    rule_id: OpaqueId
    hypothesis_id: OpaqueId
    metric_key: str
    comparator: Comparator
    threshold: float | tuple[float, float]
    missing_metric: MissingMetricPolicy = MissingMetricPolicy.INCONCLUSIVE


@dataclass(frozen=True, slots=True)
class RetroPolicy:
    """Ordered set of verification rules (channel policy pack)."""

    policy_id: OpaqueId
    rules: tuple[VerificationRule, ...]
    preferred_window_id: OpaqueId | None = None


@dataclass(frozen=True, slots=True)
class RuleEvaluation:
    """Deterministic evaluation of one rule against injected metrics."""

    rule_id: OpaqueId
    hypothesis_id: OpaqueId
    metric_key: str
    comparator: Comparator
    threshold: float | tuple[float, float]
    observed_value: float | None
    verdict: RuleVerdict
    evidence: str
    source_window_id: OpaqueId | None = None
    source_collected_at: str | None = None


@dataclass(frozen=True, slots=True)
class RetroReport:
    """Aggregate deterministic retro result for one episode + policy."""

    episode_id: OpaqueId
    policy_id: OpaqueId
    evaluations: tuple[RuleEvaluation, ...]
    checkpoint_window_ids: tuple[OpaqueId, ...]
    supported_count: int
    refuted_count: int
    inconclusive_count: int
    rules_version: str | None = None
