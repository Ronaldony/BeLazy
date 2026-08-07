"""Deterministic retro calculator tests (synthetic fixtures only)."""

from __future__ import annotations

from pathlib import Path

from video_factory.analytics import (
    AnalyticsRecord,
    CheckpointWindow,
    Comparator,
    RetroPolicy,
    RuleVerdict,
    VerificationRule,
    analytics_record_to_mapping,
    compute_retro,
    retro_report_to_mapping,
)
from video_factory.artifacts import validate_artifact
from video_factory.domain import OpaqueId, RoleId


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "video_factory"


def _record(
    *,
    episode: str = "ep-demo-100",
    window_id: str = "window-alpha",
    definition: str = "caller-defined observation window A",
    collected_at: str = "2026-07-21T12:00:00Z",
    metrics: dict[str, float | None] | None = None,
) -> AnalyticsRecord:
    return AnalyticsRecord(
        episode_id=OpaqueId(episode),
        collected_at=collected_at,
        checkpoint_window=CheckpointWindow(
            window_id=OpaqueId(window_id),
            definition=definition,
        ),
        metrics=metrics
        or {
            "retention_index": 0.42,
            "share_yield": 0.01,
        },
        collector_role=RoleId("role:human-operator"),
        rules_version="rules-bundle-test",
    )


def _policy(rules: list[VerificationRule], *, preferred: str | None = None) -> RetroPolicy:
    return RetroPolicy(
        policy_id=OpaqueId("policy-demo"),
        rules=tuple(rules),
        preferred_window_id=OpaqueId(preferred) if preferred is not None else None,
    )


def test_compute_retro_is_deterministic() -> None:
    record = _record(
        metrics={"retention_index": 0.55, "missing_metric": None},
    )
    policy = _policy(
        [
            VerificationRule(
                rule_id=OpaqueId("r1"),
                hypothesis_id=OpaqueId("h-open"),
                metric_key="retention_index",
                comparator=Comparator.GTE,
                threshold=0.5,
            ),
            VerificationRule(
                rule_id=OpaqueId("r2"),
                hypothesis_id=OpaqueId("h-closed"),
                metric_key="missing_metric",
                comparator=Comparator.GTE,
                threshold=0.1,
            ),
        ]
    )
    a = compute_retro([record], policy)
    b = compute_retro([record], policy)
    assert retro_report_to_mapping(a) == retro_report_to_mapping(b)
    assert a.evaluations[0].verdict is RuleVerdict.SUPPORTED
    assert a.evaluations[1].verdict is RuleVerdict.INCONCLUSIVE


def test_supported_refuted_inconclusive_paths() -> None:
    record = _record(
        metrics={
            "metric_high": 10.0,
            "metric_low": 1.0,
            "metric_null": None,
        }
    )
    policy = _policy(
        [
            VerificationRule(
                rule_id=OpaqueId("ok"),
                hypothesis_id=OpaqueId("hyp-a"),
                metric_key="metric_high",
                comparator=Comparator.GTE,
                threshold=5.0,
            ),
            VerificationRule(
                rule_id=OpaqueId("bad"),
                hypothesis_id=OpaqueId("hyp-b"),
                metric_key="metric_low",
                comparator=Comparator.GTE,
                threshold=5.0,
            ),
            VerificationRule(
                rule_id=OpaqueId("null"),
                hypothesis_id=OpaqueId("hyp-c"),
                metric_key="metric_null",
                comparator=Comparator.GTE,
                threshold=5.0,
            ),
            VerificationRule(
                rule_id=OpaqueId("absent"),
                hypothesis_id=OpaqueId("hyp-d"),
                metric_key="never_collected",
                comparator=Comparator.LTE,
                threshold=1.0,
            ),
        ]
    )
    report = compute_retro([record], policy)
    by_id = {str(e.rule_id): e for e in report.evaluations}
    assert by_id["ok"].verdict is RuleVerdict.SUPPORTED
    assert by_id["bad"].verdict is RuleVerdict.REFUTED
    assert by_id["null"].verdict is RuleVerdict.INCONCLUSIVE
    assert by_id["absent"].verdict is RuleVerdict.INCONCLUSIVE
    assert report.supported_count == 1
    assert report.refuted_count == 1
    assert report.inconclusive_count == 2


def test_null_metric_is_inconclusive_not_refuted() -> None:
    record = _record(metrics={"any_key": None})
    policy = _policy(
        [
            VerificationRule(
                rule_id=OpaqueId("r-null"),
                hypothesis_id=OpaqueId("hyp-null"),
                metric_key="any_key",
                comparator=Comparator.GTE,
                threshold=999.0,
            )
        ]
    )
    report = compute_retro([record], policy)
    assert report.evaluations[0].verdict is RuleVerdict.INCONCLUSIVE
    assert report.evaluations[0].observed_value is None
    assert "refuted" not in report.evaluations[0].evidence


def test_arbitrary_hypothesis_count_and_names() -> None:
    """Core must not require four named marketing reasons."""

    metrics = {f"m{i}": float(i) for i in range(6)}
    record = _record(metrics=metrics)
    rules = [
        VerificationRule(
            rule_id=OpaqueId(f"rule-{i}"),
            hypothesis_id=OpaqueId(f"custom-hypothesis-{i}"),
            metric_key=f"m{i}",
            comparator=Comparator.GTE,
            threshold=0.0,
        )
        for i in range(6)
    ]
    report = compute_retro([record], _policy(rules))
    assert len(report.evaluations) == 6
    assert report.supported_count == 6
    ids = {str(e.hypothesis_id) for e in report.evaluations}
    assert ids == {f"custom-hypothesis-{i}" for i in range(6)}


def test_between_comparator() -> None:
    record = _record(metrics={"score": 7.5})
    policy = _policy(
        [
            VerificationRule(
                rule_id=OpaqueId("band"),
                hypothesis_id=OpaqueId("hyp-band"),
                metric_key="score",
                comparator=Comparator.BETWEEN,
                threshold=(5.0, 10.0),
            )
        ]
    )
    report = compute_retro([record], policy)
    assert report.evaluations[0].verdict is RuleVerdict.SUPPORTED


def test_checkpoint_window_is_data_injected() -> None:
    record = _record(
        window_id="custom-window-x",
        definition="any caller-defined interval",
        metrics={"retention_index": 0.9},
    )
    policy = _policy(
        [
            VerificationRule(
                rule_id=OpaqueId("r1"),
                hypothesis_id=OpaqueId("h1"),
                metric_key="retention_index",
                comparator=Comparator.GTE,
                threshold=0.5,
            )
        ],
        preferred="custom-window-x",
    )
    report = compute_retro([record], policy)
    assert report.checkpoint_window_ids == (OpaqueId("custom-window-x"),)
    assert str(report.evaluations[0].source_window_id) == "custom-window-x"


def test_core_source_has_no_channel_checkpoint_or_reason_literals() -> None:
    """Prove channel policy labels are not hard-coded in analytics core source."""

    forbidden = (
        "Stop reason",
        "Stay reason",
        "Share reason",
        "Series reason",
        "24h",
        "7d",
        "28d",
    )
    hits: list[str] = []
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                hits.append(f"{path.relative_to(ROOT)}:{token}")
    assert hits == []


def test_serialized_documents_validate_against_schemas() -> None:
    record = _record(
        metrics={"retention_index": 0.8, "share_yield": None},
    )
    policy = _policy(
        [
            VerificationRule(
                rule_id=OpaqueId("r1"),
                hypothesis_id=OpaqueId("h1"),
                metric_key="retention_index",
                comparator=Comparator.GTE,
                threshold=0.5,
            ),
            VerificationRule(
                rule_id=OpaqueId("r2"),
                hypothesis_id=OpaqueId("h2"),
                metric_key="share_yield",
                comparator=Comparator.GTE,
                threshold=0.01,
            ),
        ]
    )
    report = compute_retro([record], policy)
    rec_doc = analytics_record_to_mapping(record)
    rep_doc = retro_report_to_mapping(report)
    assert validate_artifact(rec_doc).ok is True
    assert validate_artifact(rep_doc).ok is True
