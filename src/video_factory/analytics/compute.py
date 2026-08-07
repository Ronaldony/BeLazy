"""Deterministic retro calculator over injected metrics (no external I/O).

Same ``records`` + ``policy`` always produce the same ``RetroReport``.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from video_factory.domain import OpaqueId

from .contracts import (
    AnalyticsRecord,
    Comparator,
    MissingMetricPolicy,
    RetroPolicy,
    RetroReport,
    RuleEvaluation,
    RuleVerdict,
    VerificationRule,
)


class RetroComputeError(ValueError):
    """Raised when inputs are structurally invalid for evaluation."""


def _threshold_text(threshold: float | tuple[float, float]) -> str:
    if isinstance(threshold, tuple):
        return f"[{threshold[0]}, {threshold[1]}]"
    return repr(threshold)


def _normalize_threshold(
    rule: VerificationRule,
) -> float | tuple[float, float]:
    if rule.comparator is Comparator.BETWEEN:
        if not isinstance(rule.threshold, tuple) or len(rule.threshold) != 2:
            raise RetroComputeError(
                f"rule {rule.rule_id!r} comparator 'between' requires "
                f"threshold as a two-number range"
            )
        lo, hi = float(rule.threshold[0]), float(rule.threshold[1])
        if lo > hi:
            raise RetroComputeError(
                f"rule {rule.rule_id!r} between range has lo > hi: {lo} > {hi}"
            )
        return (lo, hi)
    if isinstance(rule.threshold, tuple):
        raise RetroComputeError(
            f"rule {rule.rule_id!r} comparator {rule.comparator.value!r} "
            f"requires a single numeric threshold"
        )
    return float(rule.threshold)


def _compare(
    value: float,
    comparator: Comparator,
    threshold: float | tuple[float, float],
) -> bool:
    if comparator is Comparator.GTE:
        assert isinstance(threshold, float)
        return value >= threshold
    if comparator is Comparator.LTE:
        assert isinstance(threshold, float)
        return value <= threshold
    if comparator is Comparator.GT:
        assert isinstance(threshold, float)
        return value > threshold
    if comparator is Comparator.LT:
        assert isinstance(threshold, float)
        return value < threshold
    if comparator is Comparator.EQ:
        assert isinstance(threshold, float)
        return value == threshold
    if comparator is Comparator.BETWEEN:
        assert isinstance(threshold, tuple)
        lo, hi = threshold
        return lo <= value <= hi
    raise RetroComputeError(f"unknown comparator: {comparator!r}")


def _sorted_records(
    records: Sequence[AnalyticsRecord],
) -> list[AnalyticsRecord]:
    return sorted(
        records,
        key=lambda r: (
            str(r.checkpoint_window.window_id),
            r.collected_at,
            str(r.episode_id),
        ),
    )


def _select_records(
    records: Sequence[AnalyticsRecord],
    policy: RetroPolicy,
) -> list[AnalyticsRecord]:
    if not records:
        raise RetroComputeError("compute_retro requires at least one AnalyticsRecord")
    ordered = _sorted_records(records)
    episode_ids = {str(r.episode_id) for r in ordered}
    if len(episode_ids) != 1:
        raise RetroComputeError(
            "all AnalyticsRecord inputs must share the same episode_id; "
            f"got {sorted(episode_ids)}"
        )
    preferred = policy.preferred_window_id
    if preferred is not None:
        filtered = [
            r for r in ordered if str(r.checkpoint_window.window_id) == str(preferred)
        ]
        if not filtered:
            raise RetroComputeError(
                f"no AnalyticsRecord matches preferred_window_id {preferred!r}"
            )
        return filtered
    return ordered


def _resolve_metric(
    records: Sequence[AnalyticsRecord],
    metric_key: str,
) -> tuple[float | None, AnalyticsRecord | None, bool]:
    """Return (value, source_record, key_was_present).

    Walk records in deterministic order; later non-null wins for the same key.
    If the key is present only as null, return (None, last_record_with_key, True).
    If the key is absent from every record, return (None, None, False).
    """

    found_null_source: AnalyticsRecord | None = None
    value: float | None = None
    source: AnalyticsRecord | None = None
    present = False
    for record in records:
        if metric_key not in record.metrics:
            continue
        present = True
        raw = record.metrics[metric_key]
        if raw is None:
            found_null_source = record
            continue
        value = float(raw)
        source = record
    if value is not None:
        return value, source, True
    if present:
        return None, found_null_source, True
    return None, None, False


def _evaluate_rule(
    rule: VerificationRule,
    records: Sequence[AnalyticsRecord],
) -> RuleEvaluation:
    threshold = _normalize_threshold(rule)
    observed, source, present = _resolve_metric(records, rule.metric_key)

    if not present or observed is None:
        if rule.missing_metric is not MissingMetricPolicy.INCONCLUSIVE:
            raise RetroComputeError(
                f"rule {rule.rule_id!r} uses unsupported missing_metric "
                f"{rule.missing_metric!r}"
            )
        reason = "metric key absent from records" if not present else "metric value is null"
        evidence = (
            f"inconclusive: {reason}; metric_key={rule.metric_key!r}; "
            f"comparator={rule.comparator.value}; threshold={_threshold_text(threshold)}"
        )
        return RuleEvaluation(
            rule_id=rule.rule_id,
            hypothesis_id=rule.hypothesis_id,
            metric_key=rule.metric_key,
            comparator=rule.comparator,
            threshold=threshold,
            observed_value=None,
            verdict=RuleVerdict.INCONCLUSIVE,
            evidence=evidence,
            source_window_id=(
                source.checkpoint_window.window_id if source is not None else None
            ),
            source_collected_at=source.collected_at if source is not None else None,
        )

    passed = _compare(observed, rule.comparator, threshold)
    verdict = RuleVerdict.SUPPORTED if passed else RuleVerdict.REFUTED
    evidence = (
        f"{verdict.value}: observed={observed!r} "
        f"comparator={rule.comparator.value} "
        f"threshold={_threshold_text(threshold)} "
        f"metric_key={rule.metric_key!r}"
    )
    assert source is not None
    return RuleEvaluation(
        rule_id=rule.rule_id,
        hypothesis_id=rule.hypothesis_id,
        metric_key=rule.metric_key,
        comparator=rule.comparator,
        threshold=threshold,
        observed_value=observed,
        verdict=verdict,
        evidence=evidence,
        source_window_id=source.checkpoint_window.window_id,
        source_collected_at=source.collected_at,
    )


def compute_retro(
    records: Sequence[AnalyticsRecord],
    policy: RetroPolicy,
) -> RetroReport:
    """Evaluate every policy rule against injected records (pure / deterministic)."""

    if not policy.rules:
        raise RetroComputeError("RetroPolicy.rules must be non-empty")
    selected = _select_records(records, policy)
    episode_id = selected[0].episode_id
    evaluations = tuple(_evaluate_rule(rule, selected) for rule in policy.rules)

    supported = sum(1 for e in evaluations if e.verdict is RuleVerdict.SUPPORTED)
    refuted = sum(1 for e in evaluations if e.verdict is RuleVerdict.REFUTED)
    inconclusive = sum(1 for e in evaluations if e.verdict is RuleVerdict.INCONCLUSIVE)
    window_ids = tuple(
        dict.fromkeys(OpaqueId(str(r.checkpoint_window.window_id)) for r in selected)
    )
    rules_versions = [r.rules_version for r in selected if r.rules_version]
    rules_version = rules_versions[-1] if rules_versions else None

    return RetroReport(
        episode_id=episode_id,
        policy_id=policy.policy_id,
        evaluations=evaluations,
        checkpoint_window_ids=window_ids,
        supported_count=supported,
        refuted_count=refuted,
        inconclusive_count=inconclusive,
        rules_version=rules_version,
    )


def retro_report_to_mapping(report: RetroReport) -> dict[str, object]:
    """Serialize a RetroReport to a plain mapping suitable for schema validation."""

    def _threshold_json(threshold: float | tuple[float, float]) -> object:
        if isinstance(threshold, tuple):
            return {"min": threshold[0], "max": threshold[1]}
        return threshold

    evaluations: list[dict[str, object]] = []
    for item in report.evaluations:
        row: dict[str, object] = {
            "rule_id": str(item.rule_id),
            "hypothesis_id": str(item.hypothesis_id),
            "metric_key": item.metric_key,
            "comparator": item.comparator.value,
            "threshold": _threshold_json(item.threshold),
            "observed_value": item.observed_value,
            "verdict": item.verdict.value,
            "evidence": item.evidence,
        }
        if item.source_window_id is not None:
            row["source_window_id"] = str(item.source_window_id)
        if item.source_collected_at is not None:
            row["source_collected_at"] = item.source_collected_at
        evaluations.append(row)

    document: dict[str, object] = {
        "artifact_version": "retro-report/1.0",
        "episode_id": str(report.episode_id),
        "policy_id": str(report.policy_id),
        "checkpoint_window_ids": [str(w) for w in report.checkpoint_window_ids],
        "evaluations": evaluations,
        "counts": {
            "supported": report.supported_count,
            "refuted": report.refuted_count,
            "inconclusive": report.inconclusive_count,
        },
    }
    if report.rules_version is not None:
        document["rules_version"] = report.rules_version
    return document


def analytics_record_to_mapping(record: AnalyticsRecord) -> dict[str, object]:
    """Serialize an AnalyticsRecord to a plain mapping for schema validation."""

    metrics: dict[str, float | None] = {
        key: (None if value is None else float(value))
        for key, value in record.metrics.items()
    }
    document: dict[str, object] = {
        "artifact_version": "analytics-record/1.0",
        "episode_id": str(record.episode_id),
        "collected_at": record.collected_at,
        "collector_role": str(record.collector_role),
        "checkpoint_window": {
            "window_id": str(record.checkpoint_window.window_id),
            "definition": record.checkpoint_window.definition,
        },
        "metrics": metrics,
    }
    if record.rules_version is not None:
        document["rules_version"] = record.rules_version
    if record.publish_record_ref is not None:
        document["publish_record_ref"] = {
            "path": str(record.publish_record_ref.path),
            "sha256": str(record.publish_record_ref.sha256),
            "artifact_version": str(record.publish_record_ref.artifact_version),
        }
    return document


def metrics_from_mapping(raw: Mapping[str, object]) -> dict[str, float | None]:
    """Normalize a free metric map; values must be number or null."""

    out: dict[str, float | None] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key:
            raise RetroComputeError(f"metric key must be a non-empty string: {key!r}")
        if value is None:
            out[key] = None
        elif isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RetroComputeError(
                f"metric {key!r} must be number or null; got {type(value).__name__}"
            )
        else:
            out[key] = float(value)
    return out
