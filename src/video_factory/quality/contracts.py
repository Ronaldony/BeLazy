"""Pure W05 quality aggregation and targeted-remediation contracts.

The values in this module are immutable descriptions.  In particular, an
``ArtifactReference`` or a caller-created observation never proves that media
bytes are current.  Runtime implementations must satisfy the resolver/ledger
ports, while the pure builders bind and recompute every returned artifact.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from video_factory.approvals import GateContext
from video_factory.domain import ArtifactReference, HashDigest, OpaqueId


class QualityContractError(ValueError):
    """A W05 quality artifact violates a closed, fail-closed contract."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class QualityDimension(StrEnum):
    TECHNICAL_MEDIA = "technical_media"
    VISUAL_CONFORMANCE = "visual_conformance"
    CINEMATOGRAPHY = "cinematography"
    MOTION_NATURALNESS = "motion_naturalness"
    NARRATIVE_INTENT = "narrative_intent"
    CONTINUITY = "continuity"
    AUDIO = "audio"
    EDIT_RHYTHM = "edit_rhythm"
    PLATFORM_COMPLIANCE = "platform_compliance"


class QualityVerdict(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"


class QualityBundleStatus(StrEnum):
    PASSED = "passed"
    BLOCKED = "blocked"
    INCONCLUSIVE = "inconclusive"


class RemediationStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    READY = "ready"
    ESCALATION_REQUIRED = "escalation_required"


@dataclass(frozen=True, slots=True)
class QualityPolicy:
    artifact_version: str
    policy_id: OpaqueId
    policy_sha256: HashDigest
    policy_version: str
    required_dimensions: tuple[QualityDimension, ...]
    hard_dimensions: tuple[QualityDimension, ...]
    dimension_weights_bps: tuple[tuple[QualityDimension, int], ...]
    minimum_candidate_score_bps: int
    minimum_confidence_bps: int
    minimum_margin_bps: int
    maximum_remediation_retries: int
    minimum_progress_bps: int
    require_one_human_release_approval: bool
    unknown_state_fail_closed: bool


@dataclass(frozen=True, slots=True)
class MediaSubject:
    """Exact media identity plus a trusted-current observation receipt.

    ``reference.sha256`` binds media bytes, ``byte_length`` prevents truncated
    or expanded byte substitutions, and the two observation fields bind the
    runtime's workspace snapshot and its immutable resolver receipt.  This is
    still non-authorizing structural evidence; consumers re-resolve it at the
    side-effect boundary in W06.
    """

    shot_id: OpaqueId
    component_id: OpaqueId
    reference: ArtifactReference
    byte_length: int
    workspace_observation_sha256: HashDigest
    observation_receipt_ref: ArtifactReference


@dataclass(frozen=True, slots=True)
class DimensionEvaluation:
    evaluation_id: OpaqueId
    evaluation_sha256: HashDigest
    dimension: QualityDimension
    subject: MediaSubject
    evaluator_id: OpaqueId
    evaluator_version: str
    evaluator_receipt_ref: ArtifactReference
    policy_sha256: HashDigest
    gate_context_sha256: HashDigest
    verdict: QualityVerdict
    score_bps: int
    hard_failure: bool
    safety_failure: bool
    reason_codes: tuple[str, ...]
    evidence_refs: tuple[ArtifactReference, ...]


@dataclass(frozen=True, slots=True)
class SubjectQualitySummary:
    subject: MediaSubject
    status: QualityBundleStatus
    aggregate_score_bps: int
    failed_dimensions: tuple[QualityDimension, ...]
    hard_failure_reason_codes: tuple[str, ...]
    safety_failure_reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QualityBundle:
    artifact_version: str
    bundle_id: OpaqueId
    bundle_sha256: HashDigest
    episode_id: OpaqueId
    policy_sha256: HashDigest
    gate_context: GateContext
    evaluated_at: str
    evaluations: tuple[DimensionEvaluation, ...]
    subjects: tuple[SubjectQualitySummary, ...]
    status: QualityBundleStatus
    hard_failure_reason_codes: tuple[str, ...]
    safety_failure_reason_codes: tuple[str, ...]
    authority_effect: str = "none"


@dataclass(frozen=True, slots=True)
class RemediationHistoryEntry:
    attempt_index: int
    quality_bundle_sha256: HashDigest
    failed_target_sha256: HashDigest
    aggregate_score_bps: int
    attempt_receipt_ref: ArtifactReference
    cumulative_cost_minor_units: int
    candidate_count: int


@dataclass(frozen=True, slots=True)
class RemediationTarget:
    subject: MediaSubject
    component_id: OpaqueId
    dimensions: tuple[QualityDimension, ...]
    reason_codes: tuple[str, ...]
    retry_index: int


@dataclass(frozen=True, slots=True)
class RemediationPlan:
    artifact_version: str
    plan_id: OpaqueId
    plan_sha256: HashDigest
    quality_bundle_sha256: HashDigest
    policy_sha256: HashDigest
    gate_context: GateContext
    attempt_index: int
    predecessor_receipt_ref: ArtifactReference | None
    target_set_sha256: HashDigest
    cumulative_cost_minor_units: int
    candidate_count: int
    status: RemediationStatus
    targets: tuple[RemediationTarget, ...]
    history: tuple[RemediationHistoryEntry, ...]
    reason_codes: tuple[str, ...]
    full_pipeline_rerun: bool = False
    authority_effect: str = "none"


class CurrentQualityEvidenceResolver(Protocol):
    """Trusted runtime port that independently resolves current media bytes."""

    def resolve_current(self, subject: MediaSubject, *, evaluated_at: str) -> MediaSubject | None: ...


class CurrentQualityEvaluationVerifier(Protocol):
    """Trusted runtime port for current evaluator-receipt verification.

    The implementation verifies the immutable evaluator receipt and its
    revocation/currentness state.  It must not infer trust from the fields of
    ``evaluation`` alone and it must not rerun a paid model evaluation.
    """

    def verify_current(
        self,
        evaluation: DimensionEvaluation,
        *,
        gate_context: GateContext,
        policy: QualityPolicy,
        evaluated_at: str,
    ) -> DimensionEvaluation | None: ...


class QualityEvaluatorPort(Protocol):
    """External evaluator seam; pure core validates every returned evaluation."""

    def evaluate(
        self,
        dimension: QualityDimension,
        subject: MediaSubject,
        *,
        gate_context: GateContext,
        policy: QualityPolicy,
        evaluated_at: str,
    ) -> DimensionEvaluation: ...


class RemediationAttemptLedger(Protocol):
    """W06 runtime port for atomic bounded attempt/idempotency reservation."""

    def reserve_current(
        self,
        plan: RemediationPlan,
        *,
        gate_context: GateContext,
        evaluated_at: str,
    ) -> ArtifactReference | None: ...


__all__ = [
    "CurrentQualityEvaluationVerifier",
    "CurrentQualityEvidenceResolver",
    "DimensionEvaluation",
    "MediaSubject",
    "QualityBundle",
    "QualityBundleStatus",
    "QualityContractError",
    "QualityDimension",
    "QualityEvaluatorPort",
    "QualityPolicy",
    "QualityVerdict",
    "RemediationAttemptLedger",
    "RemediationHistoryEntry",
    "RemediationPlan",
    "RemediationStatus",
    "RemediationTarget",
    "SubjectQualitySummary",
]
