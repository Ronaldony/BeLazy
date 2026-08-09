"""Strict structural parsing for W05 quality artifacts.

Loading a self-consistent document never proves current media, evaluator
authenticity, or authority.  Production consumers must additionally call the
clean recomputation/verification functions with trusted current inputs.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeAlias

from video_factory.approvals import gate_context_from_mapping
from video_factory.artifacts import validate_artifact_mapping
from video_factory.config import canonical_json_bytes
from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    HashDigest,
    OpaqueId,
    RelativeArtifactPath,
)
from video_factory.json_boundary import (
    JsonInputError,
    parse_json_bytes,
    require_json_object,
    validate_json_mapping,
)

from .aggregate import (
    QUALITY_BUNDLE_VERSION,
    REMEDIATION_PLAN_VERSION,
    quality_bundle_to_mapping,
    remediation_plan_to_mapping,
    validate_quality_bundle,
    validate_remediation_plan,
)
from .contracts import (
    DimensionEvaluation,
    MediaSubject,
    QualityBundle,
    QualityBundleStatus,
    QualityDimension,
    QualityPolicy,
    QualityVerdict,
    RemediationHistoryEntry,
    RemediationPlan,
    RemediationStatus,
    RemediationTarget,
    SubjectQualitySummary,
)
from .policy import (
    QUALITY_POLICY_VERSION,
    quality_policy_to_mapping,
    validate_quality_policy,
)


QualityArtifact: TypeAlias = QualityPolicy | QualityBundle | RemediationPlan


class QualitySerializationError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _schema_validated(
    document: Mapping[str, object],
    expected_version: str,
) -> dict[str, object]:
    try:
        normalized = dict(validate_json_mapping(document))
    except JsonInputError as error:
        raise QualitySerializationError(error.code.value, str(error)) from error
    if normalized.get("artifact_version") != expected_version:
        raise QualitySerializationError(
            "quality.serialization.version",
            f"expected {expected_version!r}",
        )
    result = validate_artifact_mapping(normalized)
    if not result.ok:
        raise QualitySerializationError(
            "quality.serialization.schema",
            "; ".join(result.error_texts),
        )
    return normalized


def _reference(value: object) -> ArtifactReference:
    assert isinstance(value, Mapping)
    return ArtifactReference(
        RelativeArtifactPath(str(value["path"])),
        HashDigest(str(value["sha256"])),
        ArtifactVersion(str(value["artifact_version"])),
    )


def _media_subject(value: object) -> MediaSubject:
    assert isinstance(value, Mapping)
    return MediaSubject(
        shot_id=OpaqueId(str(value["shot_id"])),
        component_id=OpaqueId(str(value["component_id"])),
        reference=_reference(value["reference"]),
        byte_length=int(value["byte_length"]),
        workspace_observation_sha256=HashDigest(
            str(value["workspace_observation_sha256"])
        ),
        observation_receipt_ref=_reference(value["observation_receipt_ref"]),
    )


def quality_policy_from_mapping(document: Mapping[str, object]) -> QualityPolicy:
    value = _schema_validated(document, QUALITY_POLICY_VERSION)
    weights = value["dimension_weights_bps"]
    assert isinstance(weights, list)
    policy = QualityPolicy(
        artifact_version=str(value["artifact_version"]),
        policy_id=OpaqueId(str(value["policy_id"])),
        policy_sha256=HashDigest(str(value["policy_sha256"])),
        policy_version=str(value["policy_version"]),
        required_dimensions=tuple(
            QualityDimension(str(item)) for item in value["required_dimensions"]
        ),
        hard_dimensions=tuple(
            QualityDimension(str(item)) for item in value["hard_dimensions"]
        ),
        dimension_weights_bps=tuple(
            (QualityDimension(str(item["dimension"])), int(item["weight_bps"]))
            for item in weights
        ),
        minimum_candidate_score_bps=int(value["minimum_candidate_score_bps"]),
        minimum_confidence_bps=int(value["minimum_confidence_bps"]),
        minimum_margin_bps=int(value["minimum_margin_bps"]),
        maximum_remediation_retries=int(value["maximum_remediation_retries"]),
        minimum_progress_bps=int(value["minimum_progress_bps"]),
        require_one_human_release_approval=bool(
            value["require_one_human_release_approval"]
        ),
        unknown_state_fail_closed=bool(value["unknown_state_fail_closed"]),
    )
    return validate_quality_policy(policy)


def _evaluation(value: object) -> DimensionEvaluation:
    assert isinstance(value, Mapping)
    return DimensionEvaluation(
        evaluation_id=OpaqueId(str(value["evaluation_id"])),
        evaluation_sha256=HashDigest(str(value["evaluation_sha256"])),
        dimension=QualityDimension(str(value["dimension"])),
        subject=_media_subject(value["subject"]),
        evaluator_id=OpaqueId(str(value["evaluator_id"])),
        evaluator_version=str(value["evaluator_version"]),
        evaluator_receipt_ref=_reference(value["evaluator_receipt_ref"]),
        policy_sha256=HashDigest(str(value["policy_sha256"])),
        gate_context_sha256=HashDigest(str(value["gate_context_sha256"])),
        verdict=QualityVerdict(str(value["verdict"])),
        score_bps=int(value["score_bps"]),
        hard_failure=bool(value["hard_failure"]),
        safety_failure=bool(value["safety_failure"]),
        reason_codes=tuple(str(item) for item in value["reason_codes"]),
        evidence_refs=tuple(_reference(item) for item in value["evidence_refs"]),
    )


def _summary(value: object) -> SubjectQualitySummary:
    assert isinstance(value, Mapping)
    return SubjectQualitySummary(
        subject=_media_subject(value["subject"]),
        status=QualityBundleStatus(str(value["status"])),
        aggregate_score_bps=int(value["aggregate_score_bps"]),
        failed_dimensions=tuple(
            QualityDimension(str(item)) for item in value["failed_dimensions"]
        ),
        hard_failure_reason_codes=tuple(
            str(item) for item in value["hard_failure_reason_codes"]
        ),
        safety_failure_reason_codes=tuple(
            str(item) for item in value["safety_failure_reason_codes"]
        ),
    )


def quality_bundle_from_mapping(document: Mapping[str, object]) -> QualityBundle:
    value = _schema_validated(document, QUALITY_BUNDLE_VERSION)
    context = value["gate_context"]
    assert isinstance(context, Mapping)
    bundle = QualityBundle(
        artifact_version=str(value["artifact_version"]),
        bundle_id=OpaqueId(str(value["bundle_id"])),
        bundle_sha256=HashDigest(str(value["bundle_sha256"])),
        episode_id=OpaqueId(str(value["episode_id"])),
        policy_sha256=HashDigest(str(value["policy_sha256"])),
        gate_context=gate_context_from_mapping(context),
        evaluated_at=str(value["evaluated_at"]),
        evaluations=tuple(_evaluation(item) for item in value["evaluations"]),
        subjects=tuple(_summary(item) for item in value["subjects"]),
        status=QualityBundleStatus(str(value["status"])),
        hard_failure_reason_codes=tuple(
            str(item) for item in value["hard_failure_reason_codes"]
        ),
        safety_failure_reason_codes=tuple(
            str(item) for item in value["safety_failure_reason_codes"]
        ),
        authority_effect=str(value["authority_effect"]),
    )
    return validate_quality_bundle(bundle)


def _history(value: object) -> RemediationHistoryEntry:
    assert isinstance(value, Mapping)
    return RemediationHistoryEntry(
        attempt_index=int(value["attempt_index"]),
        quality_bundle_sha256=HashDigest(str(value["quality_bundle_sha256"])),
        failed_target_sha256=HashDigest(str(value["failed_target_sha256"])),
        aggregate_score_bps=int(value["aggregate_score_bps"]),
        attempt_receipt_ref=_reference(value["attempt_receipt_ref"]),
        cumulative_cost_minor_units=int(value["cumulative_cost_minor_units"]),
        candidate_count=int(value["candidate_count"]),
    )


def _target(value: object) -> RemediationTarget:
    assert isinstance(value, Mapping)
    return RemediationTarget(
        subject=_media_subject(value["subject"]),
        component_id=OpaqueId(str(value["component_id"])),
        dimensions=tuple(
            QualityDimension(str(item)) for item in value["dimensions"]
        ),
        reason_codes=tuple(str(item) for item in value["reason_codes"]),
        retry_index=int(value["retry_index"]),
    )


def remediation_plan_from_mapping(document: Mapping[str, object]) -> RemediationPlan:
    value = _schema_validated(document, REMEDIATION_PLAN_VERSION)
    context = value["gate_context"]
    assert isinstance(context, Mapping)
    predecessor = value["predecessor_receipt_ref"]
    plan = RemediationPlan(
        artifact_version=str(value["artifact_version"]),
        plan_id=OpaqueId(str(value["plan_id"])),
        plan_sha256=HashDigest(str(value["plan_sha256"])),
        quality_bundle_sha256=HashDigest(str(value["quality_bundle_sha256"])),
        policy_sha256=HashDigest(str(value["policy_sha256"])),
        gate_context=gate_context_from_mapping(context),
        attempt_index=int(value["attempt_index"]),
        predecessor_receipt_ref=(
            _reference(predecessor) if predecessor is not None else None
        ),
        target_set_sha256=HashDigest(str(value["target_set_sha256"])),
        cumulative_cost_minor_units=int(value["cumulative_cost_minor_units"]),
        candidate_count=int(value["candidate_count"]),
        status=RemediationStatus(str(value["status"])),
        targets=tuple(_target(item) for item in value["targets"]),
        history=tuple(_history(item) for item in value["history"]),
        reason_codes=tuple(str(item) for item in value["reason_codes"]),
        full_pipeline_rerun=bool(value["full_pipeline_rerun"]),
        authority_effect=str(value["authority_effect"]),
    )
    return validate_remediation_plan(plan)


def quality_artifact_from_mapping(
    document: Mapping[str, object],
) -> QualityArtifact:
    version = document.get("artifact_version")
    if version == QUALITY_POLICY_VERSION:
        return quality_policy_from_mapping(document)
    if version == QUALITY_BUNDLE_VERSION:
        return quality_bundle_from_mapping(document)
    if version == REMEDIATION_PLAN_VERSION:
        return remediation_plan_from_mapping(document)
    raise QualitySerializationError(
        "quality.serialization.version",
        "unsupported W05 quality artifact version",
    )


def quality_artifact_from_bytes(payload: bytes) -> QualityArtifact:
    try:
        document = require_json_object(parse_json_bytes(payload))
    except JsonInputError as error:
        raise QualitySerializationError(error.code.value, str(error)) from error
    return quality_artifact_from_mapping(document)


def quality_artifact_to_mapping(value: QualityArtifact) -> dict[str, object]:
    if isinstance(value, QualityPolicy):
        return quality_policy_to_mapping(value)
    if isinstance(value, QualityBundle):
        return quality_bundle_to_mapping(value)
    if isinstance(value, RemediationPlan):
        return remediation_plan_to_mapping(value)
    raise TypeError(f"unsupported quality artifact: {type(value)!r}")


def quality_artifact_to_bytes(value: QualityArtifact) -> bytes:
    return canonical_json_bytes(quality_artifact_to_mapping(value))


__all__ = [
    "QualityArtifact",
    "QualitySerializationError",
    "quality_artifact_from_bytes",
    "quality_artifact_from_mapping",
    "quality_artifact_to_bytes",
    "quality_artifact_to_mapping",
    "quality_bundle_from_mapping",
    "quality_policy_from_mapping",
    "remediation_plan_from_mapping",
]
