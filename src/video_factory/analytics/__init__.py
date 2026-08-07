"""Post-publish analytics snapshots and deterministic retro evaluation.

CLI analytics commands are **not** wired in this package yet; callers import
``compute_retro`` / types directly. Metrics are always injected — no external
API clients live here.
"""

from .compute import (
    RetroComputeError,
    analytics_record_to_mapping,
    compute_retro,
    metrics_from_mapping,
    retro_report_to_mapping,
)
from .contracts import (
    AnalyticsRecord,
    CheckpointWindow,
    Comparator,
    MissingMetricPolicy,
    RetroPolicy,
    RetroReport,
    RuleEvaluation,
    RuleVerdict,
    VerificationRule,
)

__all__ = [
    "AnalyticsRecord",
    "CheckpointWindow",
    "Comparator",
    "MissingMetricPolicy",
    "RetroComputeError",
    "RetroPolicy",
    "RetroReport",
    "RuleEvaluation",
    "RuleVerdict",
    "VerificationRule",
    "analytics_record_to_mapping",
    "compute_retro",
    "metrics_from_mapping",
    "retro_report_to_mapping",
]
