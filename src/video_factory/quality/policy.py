"""Target-owned W05 quality, selection, remediation and release policy."""

from __future__ import annotations

from dataclasses import replace

from video_factory.config import canonical_sha256
from video_factory.domain import HashDigest, OpaqueId

from .contracts import QualityContractError, QualityDimension, QualityPolicy


QUALITY_POLICY_VERSION = "quality-policy/1.0"
POLICY_VERSION = "be-lazy-quality-policy/1.0"

_DIMENSIONS = tuple(QualityDimension)
_HARD_DIMENSIONS = (
    QualityDimension.TECHNICAL_MEDIA,
    QualityDimension.CONTINUITY,
    QualityDimension.PLATFORM_COMPLIANCE,
)
_WEIGHTS = (
    (QualityDimension.TECHNICAL_MEDIA, 1500),
    (QualityDimension.VISUAL_CONFORMANCE, 1200),
    (QualityDimension.CINEMATOGRAPHY, 1000),
    (QualityDimension.MOTION_NATURALNESS, 1000),
    (QualityDimension.NARRATIVE_INTENT, 1200),
    (QualityDimension.CONTINUITY, 1400),
    (QualityDimension.AUDIO, 1000),
    (QualityDimension.EDIT_RHYTHM, 900),
    (QualityDimension.PLATFORM_COMPLIANCE, 800),
)


def _identity(policy: QualityPolicy) -> dict[str, object]:
    return {
        "artifact_version": policy.artifact_version,
        "policy_version": policy.policy_version,
        "required_dimensions": [value.value for value in policy.required_dimensions],
        "hard_dimensions": [value.value for value in policy.hard_dimensions],
        "dimension_weights_bps": [
            {"dimension": dimension.value, "weight_bps": weight}
            for dimension, weight in policy.dimension_weights_bps
        ],
        "minimum_candidate_score_bps": policy.minimum_candidate_score_bps,
        "minimum_confidence_bps": policy.minimum_confidence_bps,
        "minimum_margin_bps": policy.minimum_margin_bps,
        "maximum_remediation_retries": policy.maximum_remediation_retries,
        "minimum_progress_bps": policy.minimum_progress_bps,
        "require_one_human_release_approval": policy.require_one_human_release_approval,
        "unknown_state_fail_closed": policy.unknown_state_fail_closed,
    }


def quality_policy_to_mapping(policy: QualityPolicy) -> dict[str, object]:
    validate_quality_policy(policy)
    return {
        **_identity(policy),
        "policy_id": str(policy.policy_id),
        "policy_sha256": str(policy.policy_sha256),
    }


def validate_quality_policy(policy: QualityPolicy) -> QualityPolicy:
    if policy.artifact_version != QUALITY_POLICY_VERSION:
        raise QualityContractError("quality.policy.version", "unsupported quality policy")
    if policy.required_dimensions != _DIMENSIONS:
        raise QualityContractError("quality.policy.dimensions", "quality policy must cover the exact nine target dimensions")
    if policy.hard_dimensions != _HARD_DIMENSIONS:
        raise QualityContractError("quality.policy.hard_dimensions", "hard quality dimensions differ from target policy")
    if policy.dimension_weights_bps != _WEIGHTS or sum(weight for _, weight in policy.dimension_weights_bps) != 10_000:
        raise QualityContractError("quality.policy.weights", "quality weights must be target-owned and sum to 10000")
    if policy.policy_version != POLICY_VERSION:
        raise QualityContractError(
            "quality.policy.policy_version",
            "quality policy version differs from the target-owned policy",
        )
    bounded = (
        policy.minimum_candidate_score_bps,
        policy.minimum_confidence_bps,
        policy.minimum_margin_bps,
        policy.minimum_progress_bps,
    )
    if any(
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        or value > 10_000
        for value in bounded
    ):
        raise QualityContractError("quality.policy.threshold", "policy thresholds must be basis points")
    if (
        not isinstance(policy.maximum_remediation_retries, int)
        or isinstance(policy.maximum_remediation_retries, bool)
        or policy.maximum_remediation_retries < 1
    ):
        raise QualityContractError("quality.policy.retries", "remediation retry limit must be positive")
    if (
        not isinstance(policy.require_one_human_release_approval, bool)
        or not isinstance(policy.unknown_state_fail_closed, bool)
        or not policy.require_one_human_release_approval
        or not policy.unknown_state_fail_closed
    ):
        raise QualityContractError("quality.policy.fail_closed", "W05 target policy must retain final human release approval and fail closed")
    digest = canonical_sha256(_identity(policy))
    if str(digest) != str(policy.policy_sha256):
        raise QualityContractError("quality.policy.digest", "quality policy digest mismatch")
    if str(policy.policy_id) != f"quality-policy-{str(digest)[:20]}":
        raise QualityContractError("quality.policy.identity", "quality policy id mismatch")
    return policy


def target_quality_policy() -> QualityPolicy:
    provisional = QualityPolicy(
        artifact_version=QUALITY_POLICY_VERSION,
        policy_id=OpaqueId("pending"),
        policy_sha256=HashDigest("0" * 64),
        policy_version=POLICY_VERSION,
        required_dimensions=_DIMENSIONS,
        hard_dimensions=_HARD_DIMENSIONS,
        dimension_weights_bps=_WEIGHTS,
        minimum_candidate_score_bps=8000,
        minimum_confidence_bps=8500,
        minimum_margin_bps=500,
        maximum_remediation_retries=2,
        minimum_progress_bps=100,
        require_one_human_release_approval=True,
        unknown_state_fail_closed=True,
    )
    digest = canonical_sha256(_identity(provisional))
    return validate_quality_policy(
        replace(
            provisional,
            policy_id=OpaqueId(f"quality-policy-{str(digest)[:20]}"),
            policy_sha256=digest,
        )
    )


def require_target_quality_policy(policy: QualityPolicy) -> QualityPolicy:
    validate_quality_policy(policy)
    if policy != target_quality_policy():
        raise QualityContractError("quality.policy.not_target_owned", "quality policy is not the exact target-owned W05 policy")
    return policy


__all__ = [
    "POLICY_VERSION",
    "QUALITY_POLICY_VERSION",
    "quality_policy_to_mapping",
    "require_target_quality_policy",
    "target_quality_policy",
    "validate_quality_policy",
]
