"""Deterministic QualityBundle aggregation and targeted remediation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace
import hashlib

from video_factory.approvals import GateContext, gate_context_sha256, gate_context_to_mapping
from video_factory.artifacts import validate_artifact_mapping
from video_factory.config import canonical_json_bytes, canonical_sha256
from video_factory.domain import ArtifactReference, HashDigest, OpaqueId
from video_factory.json_boundary import parse_rfc3339_datetime

from .contracts import (
    DimensionEvaluation,
    MediaSubject,
    QualityBundle,
    QualityBundleStatus,
    QualityContractError,
    QualityDimension,
    QualityPolicy,
    QualityVerdict,
    RemediationHistoryEntry,
    RemediationPlan,
    RemediationStatus,
    RemediationTarget,
    SubjectQualitySummary,
    CurrentQualityEvaluationVerifier,
    CurrentQualityEvidenceResolver,
)
from .policy import require_target_quality_policy, target_quality_policy
from .validation import (
    media_subject_to_mapping,
    reference_to_mapping,
    require_bps,
    require_gate_context,
    require_media_subject,
    require_reason_codes,
    require_reference,
    require_reference_consistency,
    require_sha256,
    require_token,
)


QUALITY_BUNDLE_VERSION = "quality-bundle/1.0"
REMEDIATION_PLAN_VERSION = "remediation-plan/1.0"
QUALITY_EVALUATOR_RECEIPT_VERSION = "quality-evaluation-receipt/1.0"


def _subject_key(subject: MediaSubject) -> tuple[object, ...]:
    reference = subject.reference
    receipt = subject.observation_receipt_ref
    return (
        str(subject.shot_id),
        str(subject.component_id),
        str(reference.path).casefold(),
        str(reference.sha256),
        str(reference.artifact_version),
        subject.byte_length,
        str(subject.workspace_observation_sha256),
        str(receipt.path).casefold(),
        str(receipt.sha256),
        str(receipt.artifact_version),
    )


def _evaluation_identity(value: DimensionEvaluation) -> dict[str, object]:
    return {
        "dimension": value.dimension.value,
        "subject": media_subject_to_mapping(value.subject),
        "evaluator_id": str(value.evaluator_id),
        "evaluator_version": value.evaluator_version,
        "evaluator_receipt_ref": reference_to_mapping(value.evaluator_receipt_ref),
        "policy_sha256": str(value.policy_sha256),
        "gate_context_sha256": str(value.gate_context_sha256),
        "verdict": value.verdict.value,
        "score_bps": value.score_bps,
        "hard_failure": value.hard_failure,
        "safety_failure": value.safety_failure,
        "reason_codes": list(value.reason_codes),
        "evidence_refs": [reference_to_mapping(item) for item in value.evidence_refs],
    }


def dimension_evaluation_to_mapping(value: DimensionEvaluation) -> dict[str, object]:
    validate_dimension_evaluation(value)
    return {
        **_evaluation_identity(value),
        "evaluation_id": str(value.evaluation_id),
        "evaluation_sha256": str(value.evaluation_sha256),
    }


def build_dimension_evaluation(
    *,
    dimension: QualityDimension,
    subject: MediaSubject,
    evaluator_id: str,
    evaluator_version: str,
    evaluator_receipt_ref: ArtifactReference,
    gate_context: GateContext,
    policy: QualityPolicy,
    verdict: QualityVerdict,
    score_bps: int,
    hard_failure: bool = False,
    safety_failure: bool = False,
    reason_codes: Sequence[str] = (),
    evidence_refs: Sequence[ArtifactReference] = (),
) -> DimensionEvaluation:
    require_target_quality_policy(policy)
    require_gate_context(gate_context)
    if not isinstance(hard_failure, bool) or not isinstance(safety_failure, bool):
        raise QualityContractError(
            "quality.evaluation.failure_flag",
            "hard_failure and safety_failure must be booleans",
        )
    provisional = DimensionEvaluation(
        evaluation_id=OpaqueId("pending"),
        evaluation_sha256=HashDigest("0" * 64),
        dimension=QualityDimension(dimension),
        subject=require_media_subject(subject, "subject"),
        evaluator_id=OpaqueId(require_token(evaluator_id, "evaluator_id")),
        evaluator_version=require_token(evaluator_version, "evaluator_version"),
        evaluator_receipt_ref=require_reference(
            evaluator_receipt_ref,
            "evaluator_receipt_ref",
        ),
        policy_sha256=policy.policy_sha256,
        gate_context_sha256=gate_context_sha256(gate_context),
        verdict=QualityVerdict(verdict),
        score_bps=require_bps(score_bps, "score_bps"),
        hard_failure=hard_failure,
        safety_failure=safety_failure,
        reason_codes=tuple(sorted(set(reason_codes))),
        evidence_refs=tuple(evidence_refs),
    )
    digest = canonical_sha256(_evaluation_identity(provisional))
    return validate_dimension_evaluation(
        replace(
            provisional,
            evaluation_id=OpaqueId(f"quality-evaluation-{str(digest)[:20]}"),
            evaluation_sha256=digest,
        )
    )


def validate_dimension_evaluation(value: DimensionEvaluation) -> DimensionEvaluation:
    require_media_subject(value.subject, "subject")
    require_token(str(value.evaluator_id), "evaluator_id")
    require_token(value.evaluator_version, "evaluator_version")
    require_reference(value.evaluator_receipt_ref, "evaluator_receipt_ref")
    if value.evaluator_receipt_ref.artifact_version != QUALITY_EVALUATOR_RECEIPT_VERSION:
        raise QualityContractError(
            "quality.evaluation.receipt_version",
            "evaluator receipt has an unsupported artifact version",
        )
    policy = require_target_quality_policy(target_quality_policy())
    if value.policy_sha256 != policy.policy_sha256:
        raise QualityContractError(
            "quality.evaluation.policy",
            "dimension evaluation uses another quality policy",
        )
    require_sha256(str(value.gate_context_sha256), "gate_context_sha256")
    require_bps(value.score_bps, "score_bps")
    if not isinstance(value.hard_failure, bool) or not isinstance(
        value.safety_failure, bool
    ):
        raise QualityContractError(
            "quality.evaluation.failure_flag",
            "hard_failure and safety_failure must be booleans",
        )
    require_reason_codes(
        value.reason_codes,
        "reason_codes",
        allow_empty=value.verdict is QualityVerdict.PASS,
    )
    require_reference_consistency(
        (
            value.subject.reference,
            value.subject.observation_receipt_ref,
            value.evaluator_receipt_ref,
            *value.evidence_refs,
        ),
        allow_exact_reuse=False,
    )
    if value.verdict is QualityVerdict.PASS and (
        value.hard_failure or value.safety_failure
    ):
        raise QualityContractError(
            "quality.evaluation.verdict",
            "PASS cannot carry a hard or safety failure",
        )
    if (value.hard_failure or value.safety_failure) and not value.reason_codes:
        raise QualityContractError(
            "quality.evaluation.reasons",
            "hard and safety failures require stable reasons",
        )
    digest = canonical_sha256(_evaluation_identity(value))
    if str(digest) != str(value.evaluation_sha256):
        raise QualityContractError(
            "quality.evaluation.digest",
            "dimension evaluation digest mismatch",
        )
    if str(value.evaluation_id) != f"quality-evaluation-{str(digest)[:20]}":
        raise QualityContractError(
            "quality.evaluation.identity",
            "dimension evaluation id mismatch",
        )
    return value


def _summary_mapping(value: SubjectQualitySummary) -> dict[str, object]:
    return {
        "subject": media_subject_to_mapping(value.subject),
        "status": value.status.value,
        "aggregate_score_bps": value.aggregate_score_bps,
        "failed_dimensions": [item.value for item in value.failed_dimensions],
        "hard_failure_reason_codes": list(value.hard_failure_reason_codes),
        "safety_failure_reason_codes": list(value.safety_failure_reason_codes),
    }


def _summaries(
    evaluations: tuple[DimensionEvaluation, ...],
    policy: QualityPolicy,
) -> tuple[SubjectQualitySummary, ...]:
    by_subject: dict[tuple[object, ...], list[DimensionEvaluation]] = defaultdict(list)
    subject_by_key: dict[tuple[object, ...], MediaSubject] = {}
    for value in evaluations:
        key = _subject_key(value.subject)
        by_subject[key].append(value)
        subject_by_key[key] = value.subject
    weights = dict(policy.dimension_weights_bps)
    summaries: list[SubjectQualitySummary] = []
    for key in sorted(by_subject):
        items = sorted(by_subject[key], key=lambda item: item.dimension.value)
        dimensions = tuple(item.dimension for item in items)
        if (
            set(dimensions) != set(policy.required_dimensions)
            or len(dimensions) != len(policy.required_dimensions)
        ):
            raise QualityContractError(
                "quality.bundle.coverage",
                "each media subject needs exactly one evaluation for all nine dimensions",
            )
        if len({str(item.evaluator_id) for item in items}) != len(items):
            raise QualityContractError(
                "quality.bundle.evaluator_independence",
                "each dimension for one media subject needs a distinct evaluator identity",
            )
        failed = tuple(
            sorted(
                {
                    item.dimension
                    for item in items
                    if item.verdict in {QualityVerdict.FAIL, QualityVerdict.INCONCLUSIVE}
                    or item.hard_failure
                    or item.safety_failure
                    or (
                        item.dimension in policy.hard_dimensions
                        and item.verdict is not QualityVerdict.PASS
                    )
                },
                key=lambda item: item.value,
            )
        )
        hard_codes = tuple(
            sorted(
                {
                    code
                    for item in items
                    if item.hard_failure
                    or (
                        item.dimension in policy.hard_dimensions
                        and item.verdict is not QualityVerdict.PASS
                    )
                    for code in item.reason_codes
                }
            )
        )
        safety_codes = tuple(
            sorted(
                {
                    code
                    for item in items
                    if item.safety_failure
                    for code in item.reason_codes
                }
            )
        )
        if hard_codes or safety_codes or any(
            item.verdict is QualityVerdict.FAIL for item in items
        ):
            status = QualityBundleStatus.BLOCKED
        elif any(item.verdict is QualityVerdict.INCONCLUSIVE for item in items):
            status = QualityBundleStatus.INCONCLUSIVE
        else:
            status = QualityBundleStatus.PASSED
        score = sum(
            item.score_bps * weights[item.dimension] for item in items
        ) // 10_000
        summaries.append(
            SubjectQualitySummary(
                subject=subject_by_key[key],
                status=status,
                aggregate_score_bps=score,
                failed_dimensions=failed,
                hard_failure_reason_codes=hard_codes,
                safety_failure_reason_codes=safety_codes,
            )
        )
    return tuple(summaries)


def _bundle_identity(value: QualityBundle) -> dict[str, object]:
    return {
        "artifact_version": value.artifact_version,
        "episode_id": str(value.episode_id),
        "policy_sha256": str(value.policy_sha256),
        "gate_context": gate_context_to_mapping(value.gate_context),
        "evaluated_at": value.evaluated_at,
        "evaluations": [
            dimension_evaluation_to_mapping(item) for item in value.evaluations
        ],
        "subjects": [_summary_mapping(item) for item in value.subjects],
        "status": value.status.value,
        "hard_failure_reason_codes": list(value.hard_failure_reason_codes),
        "safety_failure_reason_codes": list(value.safety_failure_reason_codes),
        "authority_effect": value.authority_effect,
    }


def quality_bundle_to_mapping(value: QualityBundle) -> dict[str, object]:
    validate_quality_bundle(value)
    mapping = {
        **_bundle_identity(value),
        "bundle_id": str(value.bundle_id),
        "bundle_sha256": str(value.bundle_sha256),
    }
    result = validate_artifact_mapping(mapping)
    if not result.ok:
        raise QualityContractError("quality.bundle.schema", "; ".join(result.error_texts))
    return mapping


def build_quality_bundle(
    *,
    episode_id: str,
    gate_context: GateContext,
    evaluations: Sequence[DimensionEvaluation],
    policy: QualityPolicy,
    evaluated_at: str,
) -> QualityBundle:
    require_target_quality_policy(policy)
    require_gate_context(gate_context)
    parse_rfc3339_datetime(evaluated_at)
    ordered = tuple(
        sorted(
            evaluations,
            key=lambda item: (
                _subject_key(item.subject),
                item.dimension.value,
                str(item.evaluator_id),
            ),
        )
    )
    if not ordered:
        raise QualityContractError(
            "quality.bundle.empty",
            "quality bundle needs evaluations",
        )
    evaluator_receipts: list[ArtifactReference] = []
    all_references: list[ArtifactReference] = []
    for value in ordered:
        validate_dimension_evaluation(value)
        if value.gate_context_sha256 != gate_context_sha256(gate_context):
            raise QualityContractError(
                "quality.bundle.context",
                "dimension evaluation is bound to another current context",
            )
        evaluator_receipts.append(value.evaluator_receipt_ref)
        all_references.extend(
            (
                value.subject.reference,
                value.subject.observation_receipt_ref,
                value.evaluator_receipt_ref,
                *value.evidence_refs,
            )
        )
    require_reference_consistency(evaluator_receipts, allow_exact_reuse=False)
    require_reference_consistency(all_references, allow_exact_reuse=True)
    summaries = _summaries(ordered, policy)
    hard_codes = tuple(
        sorted(
            {
                code
                for item in summaries
                for code in item.hard_failure_reason_codes
            }
        )
    )
    safety_codes = tuple(
        sorted(
            {
                code
                for item in summaries
                for code in item.safety_failure_reason_codes
            }
        )
    )
    if any(item.status is QualityBundleStatus.BLOCKED for item in summaries):
        status = QualityBundleStatus.BLOCKED
    elif any(
        item.status is QualityBundleStatus.INCONCLUSIVE for item in summaries
    ):
        status = QualityBundleStatus.INCONCLUSIVE
    else:
        status = QualityBundleStatus.PASSED
    provisional = QualityBundle(
        artifact_version=QUALITY_BUNDLE_VERSION,
        bundle_id=OpaqueId("pending"),
        bundle_sha256=HashDigest("0" * 64),
        episode_id=OpaqueId(require_token(episode_id, "episode_id")),
        policy_sha256=policy.policy_sha256,
        gate_context=gate_context,
        evaluated_at=evaluated_at,
        evaluations=ordered,
        subjects=summaries,
        status=status,
        hard_failure_reason_codes=hard_codes,
        safety_failure_reason_codes=safety_codes,
        authority_effect="none",
    )
    digest = canonical_sha256(_bundle_identity(provisional))
    return validate_quality_bundle(
        replace(
            provisional,
            bundle_id=OpaqueId(f"quality-bundle-{str(digest)[:20]}"),
            bundle_sha256=digest,
        )
    )


def validate_quality_bundle(value: QualityBundle) -> QualityBundle:
    if (
        value.artifact_version != QUALITY_BUNDLE_VERSION
        or value.authority_effect != "none"
    ):
        raise QualityContractError(
            "quality.bundle.contract",
            "invalid non-authorizing QualityBundle contract",
        )
    require_token(str(value.episode_id), "episode_id")
    policy = require_target_quality_policy(target_quality_policy())
    if value.policy_sha256 != policy.policy_sha256:
        raise QualityContractError(
            "quality.bundle.policy",
            "QualityBundle uses another policy",
        )
    require_gate_context(value.gate_context)
    parse_rfc3339_datetime(value.evaluated_at)
    ordered = tuple(
        sorted(
            value.evaluations,
            key=lambda item: (
                _subject_key(item.subject),
                item.dimension.value,
                str(item.evaluator_id),
            ),
        )
    )
    if not ordered or value.evaluations != ordered:
        raise QualityContractError(
            "quality.bundle.order",
            "dimension evaluations are empty or non-canonical",
        )
    evaluator_receipts: list[ArtifactReference] = []
    all_references: list[ArtifactReference] = []
    for item in value.evaluations:
        validate_dimension_evaluation(item)
        if item.gate_context_sha256 != gate_context_sha256(value.gate_context):
            raise QualityContractError(
                "quality.bundle.context",
                "dimension evaluation context mismatch",
            )
        evaluator_receipts.append(item.evaluator_receipt_ref)
        all_references.extend(
            (
                item.subject.reference,
                item.subject.observation_receipt_ref,
                item.evaluator_receipt_ref,
                *item.evidence_refs,
            )
        )
    require_reference_consistency(evaluator_receipts, allow_exact_reuse=False)
    require_reference_consistency(all_references, allow_exact_reuse=True)
    expected_summaries = _summaries(value.evaluations, policy)
    if value.subjects != expected_summaries:
        raise QualityContractError(
            "quality.bundle.summary",
            "subject summaries do not match independent evaluations",
        )
    hard_codes = tuple(
        sorted(
            {
                code
                for item in value.subjects
                for code in item.hard_failure_reason_codes
            }
        )
    )
    safety_codes = tuple(
        sorted(
            {
                code
                for item in value.subjects
                for code in item.safety_failure_reason_codes
            }
        )
    )
    expected_status = QualityBundleStatus.PASSED
    if any(item.status is QualityBundleStatus.BLOCKED for item in value.subjects):
        expected_status = QualityBundleStatus.BLOCKED
    elif any(
        item.status is QualityBundleStatus.INCONCLUSIVE for item in value.subjects
    ):
        expected_status = QualityBundleStatus.INCONCLUSIVE
    if (
        value.status is not expected_status
        or value.hard_failure_reason_codes != hard_codes
        or value.safety_failure_reason_codes != safety_codes
    ):
        raise QualityContractError(
            "quality.bundle.status",
            "QualityBundle status does not preserve hard and safety failures",
        )
    digest = canonical_sha256(_bundle_identity(value))
    if (
        str(digest) != str(value.bundle_sha256)
        or str(value.bundle_id) != f"quality-bundle-{str(digest)[:20]}"
    ):
        raise QualityContractError(
            "quality.bundle.digest",
            "QualityBundle identity mismatch",
        )
    return value


def verify_quality_bundle(
    value: QualityBundle,
    *,
    current_context: GateContext,
    policy: QualityPolicy,
    resolver: CurrentQualityEvidenceResolver,
    evaluation_verifier: CurrentQualityEvaluationVerifier,
    evaluated_at: str,
) -> QualityBundle:
    """Re-resolve exact media observations and cleanly recompute the bundle."""

    validate_quality_bundle(value)
    require_target_quality_policy(policy)
    require_gate_context(current_context)
    parse_rfc3339_datetime(evaluated_at)
    if value.gate_context != current_context or value.policy_sha256 != policy.policy_sha256:
        raise QualityContractError(
            "quality.bundle.stale",
            "QualityBundle is bound to another current context or policy",
        )
    seen: set[tuple[object, ...]] = set()
    for evaluation in value.evaluations:
        key = _subject_key(evaluation.subject)
        if key not in seen:
            seen.add(key)
            try:
                current = resolver.resolve_current(
                    evaluation.subject,
                    evaluated_at=evaluated_at,
                )
            except Exception as error:
                raise QualityContractError(
                    "quality.bundle.resolver_unavailable",
                    "current media resolver failed",
                ) from error
            if current != evaluation.subject:
                raise QualityContractError(
                    "quality.bundle.media_stale",
                    "quality evidence is not bound to current exact media bytes",
                )
        try:
            verified_evaluation = evaluation_verifier.verify_current(
                evaluation,
                gate_context=current_context,
                policy=policy,
                evaluated_at=evaluated_at,
            )
        except Exception as error:
            raise QualityContractError(
                "quality.bundle.evaluation_verifier_unavailable",
                "current evaluator-receipt verification failed",
            ) from error
        if verified_evaluation != evaluation:
            raise QualityContractError(
                "quality.bundle.evaluation_stale",
                "quality evaluation receipt is missing, stale, revoked, or rebound",
            )
    expected = build_quality_bundle(
        episode_id=str(value.episode_id),
        gate_context=current_context,
        evaluations=value.evaluations,
        policy=policy,
        evaluated_at=value.evaluated_at,
    )
    if value != expected:
        raise QualityContractError(
            "quality.bundle.semantic_rebound",
            "QualityBundle differs from clean current recomputation",
        )
    return value


def quality_bundle_bytes_sha256(value: QualityBundle) -> HashDigest:
    return HashDigest(
        hashlib.sha256(canonical_json_bytes(quality_bundle_to_mapping(value))).hexdigest()
    )


def _failed_target_mapping(bundle: QualityBundle) -> list[dict[str, object]]:
    return [
        {
            "subject": media_subject_to_mapping(summary.subject),
            "dimensions": [item.value for item in summary.failed_dimensions],
        }
        for summary in bundle.subjects
        if summary.failed_dimensions
    ]


def remediation_history_entry(
    bundle: QualityBundle,
    *,
    attempt_index: int,
    attempt_receipt_ref: ArtifactReference,
    cumulative_cost_minor_units: int,
    candidate_count: int,
) -> RemediationHistoryEntry:
    validate_quality_bundle(bundle)
    if attempt_index < 0:
        raise QualityContractError(
            "quality.remediation.attempt",
            "attempt index must be non-negative",
        )
    require_reference(attempt_receipt_ref, "attempt_receipt_ref")
    if attempt_receipt_ref.artifact_version != "remediation-attempt-receipt/1.0":
        raise QualityContractError(
            "quality.remediation.receipt_version",
            "attempt receipt has an unsupported version",
        )
    _require_non_negative(cumulative_cost_minor_units, "cumulative_cost_minor_units")
    _require_non_negative(candidate_count, "candidate_count")
    average = sum(item.aggregate_score_bps for item in bundle.subjects) // len(
        bundle.subjects
    )
    return RemediationHistoryEntry(
        attempt_index=attempt_index,
        quality_bundle_sha256=bundle.bundle_sha256,
        failed_target_sha256=canonical_sha256(_failed_target_mapping(bundle)),
        aggregate_score_bps=average,
        attempt_receipt_ref=attempt_receipt_ref,
        cumulative_cost_minor_units=cumulative_cost_minor_units,
        candidate_count=candidate_count,
    )


def _require_non_negative(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise QualityContractError(
            "quality.non_negative",
            f"{label} must be a non-negative integer",
        )
    return value


def _target_mapping(value: RemediationTarget) -> dict[str, object]:
    return {
        "subject": media_subject_to_mapping(value.subject),
        "component_id": str(value.component_id),
        "dimensions": [item.value for item in value.dimensions],
        "reason_codes": list(value.reason_codes),
        "retry_index": value.retry_index,
    }


def _history_mapping(value: RemediationHistoryEntry) -> dict[str, object]:
    _validate_history_entry(value)
    return {
        "attempt_index": value.attempt_index,
        "quality_bundle_sha256": str(value.quality_bundle_sha256),
        "failed_target_sha256": str(value.failed_target_sha256),
        "aggregate_score_bps": value.aggregate_score_bps,
        "attempt_receipt_ref": reference_to_mapping(value.attempt_receipt_ref),
        "cumulative_cost_minor_units": value.cumulative_cost_minor_units,
        "candidate_count": value.candidate_count,
    }


def _validate_history_entry(value: RemediationHistoryEntry) -> RemediationHistoryEntry:
    _require_non_negative(value.attempt_index, "history.attempt_index")
    require_sha256(str(value.quality_bundle_sha256), "history.quality_bundle_sha256")
    require_sha256(str(value.failed_target_sha256), "history.failed_target_sha256")
    require_bps(value.aggregate_score_bps, "history.aggregate_score_bps")
    _require_non_negative(
        value.cumulative_cost_minor_units,
        "history.cumulative_cost_minor_units",
    )
    _require_non_negative(value.candidate_count, "history.candidate_count")
    require_reference(value.attempt_receipt_ref, "history.attempt_receipt_ref")
    if value.attempt_receipt_ref.artifact_version != "remediation-attempt-receipt/1.0":
        raise QualityContractError(
            "quality.remediation.receipt_version",
            "attempt receipt has an unsupported version",
        )
    return value


def _remediation_identity(value: RemediationPlan) -> dict[str, object]:
    return {
        "artifact_version": value.artifact_version,
        "quality_bundle_sha256": str(value.quality_bundle_sha256),
        "policy_sha256": str(value.policy_sha256),
        "gate_context": gate_context_to_mapping(value.gate_context),
        "attempt_index": value.attempt_index,
        "predecessor_receipt_ref": (
            reference_to_mapping(value.predecessor_receipt_ref)
            if value.predecessor_receipt_ref is not None
            else None
        ),
        "target_set_sha256": str(value.target_set_sha256),
        "cumulative_cost_minor_units": value.cumulative_cost_minor_units,
        "candidate_count": value.candidate_count,
        "status": value.status.value,
        "targets": [_target_mapping(item) for item in value.targets],
        "history": [_history_mapping(item) for item in value.history],
        "reason_codes": list(value.reason_codes),
        "full_pipeline_rerun": value.full_pipeline_rerun,
        "authority_effect": value.authority_effect,
    }


def remediation_plan_to_mapping(value: RemediationPlan) -> dict[str, object]:
    validate_remediation_plan(value)
    mapping = {
        **_remediation_identity(value),
        "plan_id": str(value.plan_id),
        "plan_sha256": str(value.plan_sha256),
    }
    result = validate_artifact_mapping(mapping)
    if not result.ok:
        raise QualityContractError("quality.remediation.schema", "; ".join(result.error_texts))
    return mapping


def plan_targeted_remediation(
    bundle: QualityBundle,
    *,
    history: Sequence[RemediationHistoryEntry] = (),
    policy: QualityPolicy,
    cumulative_cost_minor_units: int = 0,
    candidate_count: int = 0,
) -> RemediationPlan:
    validate_quality_bundle(bundle)
    require_target_quality_policy(policy)
    if bundle.policy_sha256 != policy.policy_sha256:
        raise QualityContractError(
            "quality.remediation.policy",
            "bundle and remediation policy differ",
        )
    _require_non_negative(cumulative_cost_minor_units, "cumulative_cost_minor_units")
    _require_non_negative(candidate_count, "candidate_count")
    prior = tuple(history)
    for expected_index, item in enumerate(prior):
        _validate_history_entry(item)
        if item.attempt_index != expected_index:
            raise QualityContractError(
                "quality.remediation.history",
                "remediation history attempt indexes must be contiguous",
            )
        if expected_index and (
            item.cumulative_cost_minor_units < prior[expected_index - 1].cumulative_cost_minor_units
            or item.candidate_count < prior[expected_index - 1].candidate_count
        ):
            raise QualityContractError(
                "quality.remediation.budget_regression",
                "remediation cumulative usage cannot decrease",
            )
    require_reference_consistency(
        tuple(item.attempt_receipt_ref for item in prior),
        allow_exact_reuse=False,
    )
    if prior and (
        cumulative_cost_minor_units < prior[-1].cumulative_cost_minor_units
        or candidate_count < prior[-1].candidate_count
    ):
        raise QualityContractError(
            "quality.remediation.budget_regression",
            "current cumulative usage cannot decrease",
        )
    failed_fingerprint = canonical_sha256(_failed_target_mapping(bundle))
    current_average = sum(item.aggregate_score_bps for item in bundle.subjects) // len(
        bundle.subjects
    )
    reasons: list[str] = []
    status = RemediationStatus.READY
    retry_index = len(prior)
    if not any(summary.failed_dimensions for summary in bundle.subjects):
        status = RemediationStatus.NOT_REQUIRED
        reasons.append("quality.remediation.not_required")
    elif retry_index >= policy.maximum_remediation_retries:
        status = RemediationStatus.ESCALATION_REQUIRED
        reasons.append("quality.remediation.retry_exhausted")
    elif any(item.quality_bundle_sha256 == bundle.bundle_sha256 for item in prior):
        status = RemediationStatus.ESCALATION_REQUIRED
        reasons.append("quality.remediation.replay")
    elif any(item.failed_target_sha256 == failed_fingerprint for item in prior):
        status = RemediationStatus.ESCALATION_REQUIRED
        reasons.append("quality.remediation.oscillation")
    elif prior and current_average < prior[-1].aggregate_score_bps:
        status = RemediationStatus.ESCALATION_REQUIRED
        reasons.append("quality.remediation.regression")
    elif (
        prior
        and current_average - prior[-1].aggregate_score_bps
        < policy.minimum_progress_bps
    ):
        status = RemediationStatus.ESCALATION_REQUIRED
        reasons.append("quality.remediation.no_progress")
    targets: list[RemediationTarget] = []
    if status is RemediationStatus.READY:
        by_subject = {_subject_key(item.subject): item for item in bundle.subjects}
        for key in sorted(by_subject):
            summary = by_subject[key]
            if not summary.failed_dimensions:
                continue
            codes = tuple(
                sorted(
                    {
                        code
                        for evaluation in bundle.evaluations
                        if _subject_key(evaluation.subject) == key
                        and evaluation.dimension in summary.failed_dimensions
                        for code in evaluation.reason_codes
                    }
                )
            )
            targets.append(
                RemediationTarget(
                    subject=summary.subject,
                    component_id=summary.subject.component_id,
                    dimensions=summary.failed_dimensions,
                    reason_codes=codes,
                    retry_index=retry_index,
                )
            )
    target_set_sha256 = canonical_sha256(
        [_target_mapping(item) for item in targets]
    )
    predecessor = prior[-1].attempt_receipt_ref if prior else None
    provisional = RemediationPlan(
        artifact_version=REMEDIATION_PLAN_VERSION,
        plan_id=OpaqueId("pending"),
        plan_sha256=HashDigest("0" * 64),
        quality_bundle_sha256=bundle.bundle_sha256,
        policy_sha256=policy.policy_sha256,
        gate_context=bundle.gate_context,
        attempt_index=retry_index,
        predecessor_receipt_ref=predecessor,
        target_set_sha256=target_set_sha256,
        cumulative_cost_minor_units=cumulative_cost_minor_units,
        candidate_count=candidate_count,
        status=status,
        targets=tuple(targets),
        history=prior,
        reason_codes=tuple(sorted(reasons)),
        full_pipeline_rerun=False,
        authority_effect="none",
    )
    digest = canonical_sha256(_remediation_identity(provisional))
    return validate_remediation_plan(
        replace(
            provisional,
            plan_id=OpaqueId(f"remediation-plan-{str(digest)[:20]}"),
            plan_sha256=digest,
        )
    )


def validate_remediation_plan(value: RemediationPlan) -> RemediationPlan:
    if (
        value.artifact_version != REMEDIATION_PLAN_VERSION
        or value.authority_effect != "none"
        or value.full_pipeline_rerun
    ):
        raise QualityContractError(
            "quality.remediation.contract",
            "remediation plan must be targeted and non-authorizing",
        )
    require_sha256(str(value.quality_bundle_sha256), "quality_bundle_sha256")
    policy = require_target_quality_policy(target_quality_policy())
    if value.policy_sha256 != policy.policy_sha256:
        raise QualityContractError(
            "quality.remediation.policy",
            "remediation plan uses another policy",
        )
    require_gate_context(value.gate_context)
    _require_non_negative(value.attempt_index, "attempt_index")
    _require_non_negative(
        value.cumulative_cost_minor_units,
        "cumulative_cost_minor_units",
    )
    _require_non_negative(value.candidate_count, "candidate_count")
    for expected_index, item in enumerate(value.history):
        _validate_history_entry(item)
        if item.attempt_index != expected_index:
            raise QualityContractError(
                "quality.remediation.history",
                "history attempt indexes are not contiguous",
            )
    if value.attempt_index != len(value.history):
        raise QualityContractError(
            "quality.remediation.attempt",
            "plan attempt index differs from its history length",
        )
    expected_predecessor = (
        value.history[-1].attempt_receipt_ref if value.history else None
    )
    if value.predecessor_receipt_ref != expected_predecessor:
        raise QualityContractError(
            "quality.remediation.predecessor",
            "plan is not bound to the exact previous attempt receipt",
        )
    require_sha256(str(value.target_set_sha256), "target_set_sha256")
    require_reason_codes(
        value.reason_codes,
        "reason_codes",
        allow_empty=value.status is RemediationStatus.READY,
    )
    if value.status is RemediationStatus.READY and not value.targets:
        raise QualityContractError(
            "quality.remediation.targets",
            "ready remediation plan needs affected targets",
        )
    if value.status is not RemediationStatus.READY and value.targets:
        raise QualityContractError(
            "quality.remediation.targets",
            "non-ready remediation plan cannot target execution",
        )
    references: list[ArtifactReference] = []
    ordered_targets = tuple(
        sorted(
            value.targets,
            key=lambda item: (
                _subject_key(item.subject),
                str(item.component_id),
                tuple(value.value for value in item.dimensions),
            ),
        )
    )
    if value.targets != ordered_targets:
        raise QualityContractError(
            "quality.remediation.order",
            "remediation targets are not canonical",
        )
    for item in value.targets:
        require_media_subject(item.subject, "remediation subject")
        references.extend(
            (item.subject.reference, item.subject.observation_receipt_ref)
        )
        require_token(str(item.component_id), "component_id")
        if item.component_id != item.subject.component_id:
            raise QualityContractError(
                "quality.remediation.component",
                "target component does not match the exact media subject",
            )
        if (
            not item.dimensions
            or item.dimensions
            != tuple(sorted(set(item.dimensions), key=lambda value: value.value))
        ):
            raise QualityContractError(
                "quality.remediation.dimensions",
                "target dimensions must be sorted and unique",
            )
        require_reason_codes(item.reason_codes, "target.reason_codes", allow_empty=False)
        if (
            item.retry_index != value.attempt_index
            or item.retry_index >= policy.maximum_remediation_retries
        ):
            raise QualityContractError(
                "quality.remediation.retry",
                "target retry index is outside policy",
            )
    require_reference_consistency(references, allow_exact_reuse=True)
    expected_target_set = canonical_sha256(
        [_target_mapping(item) for item in value.targets]
    )
    if value.target_set_sha256 != expected_target_set:
        raise QualityContractError(
            "quality.remediation.target_set",
            "target-set digest mismatch",
        )
    digest = canonical_sha256(_remediation_identity(value))
    if (
        str(digest) != str(value.plan_sha256)
        or str(value.plan_id) != f"remediation-plan-{str(digest)[:20]}"
    ):
        raise QualityContractError(
            "quality.remediation.digest",
            "remediation plan identity mismatch",
        )
    return value


__all__ = [
    "QUALITY_BUNDLE_VERSION",
    "QUALITY_EVALUATOR_RECEIPT_VERSION",
    "REMEDIATION_PLAN_VERSION",
    "build_dimension_evaluation",
    "build_quality_bundle",
    "dimension_evaluation_to_mapping",
    "plan_targeted_remediation",
    "quality_bundle_bytes_sha256",
    "quality_bundle_to_mapping",
    "remediation_history_entry",
    "remediation_plan_to_mapping",
    "validate_dimension_evaluation",
    "validate_quality_bundle",
    "verify_quality_bundle",
    "validate_remediation_plan",
]
