"""Non-publishing W05 release candidate and assessment contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from video_factory.approvals import GateContext
from video_factory.authority import (
    ActionAuthorityRequest,
    ActionRiskAssessment,
    ApprovalRequest,
    AuthorityDecision,
    AuthorityVerificationReceipt,
)
from video_factory.domain import ArtifactReference, HashDigest, OpaqueId
from video_factory.quality import (
    CurrentQualityEvaluationVerifier,
    CurrentQualityEvidenceResolver,
    MediaSubject,
    QualityBundle,
    QualityPolicy,
)
from video_factory.selection import (
    CandidateDecision,
    CandidateDecisionVerificationInputs,
)


class ReleaseContractError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class ReleaseVisibility(StrEnum):
    PRIVATE = "private"
    UNLISTED = "unlisted"
    PUBLIC = "public"


class ReleaseAssessmentStatus(StrEnum):
    READY = "ready"
    APPROVAL_REQUIRED = "approval_required"
    DENIED = "denied"


@dataclass(frozen=True, slots=True)
class DestinationBinding:
    artifact_version: str
    destination_id: OpaqueId
    destination_sha256: HashDigest
    platform: str
    channel_account_id: OpaqueId
    visibility: ReleaseVisibility
    locale: str
    region: str
    scheduled_for: str | None
    policy_sha256: HashDigest
    authority_effect: str = "none"


@dataclass(frozen=True, slots=True)
class ReleaseCandidate:
    artifact_version: str
    candidate_id: OpaqueId
    candidate_sha256: HashDigest
    workspace_id: OpaqueId
    channel_id: OpaqueId
    concept_id: OpaqueId
    episode_id: OpaqueId
    final_media: MediaSubject
    metadata_ref: ArtifactReference
    subtitle_accessibility_refs: tuple[ArtifactReference, ...]
    thumbnail: MediaSubject
    quality_bundle_ref: ArtifactReference
    quality_bundle_sha256: HashDigest
    candidate_decision_ref: ArtifactReference
    candidate_decision_sha256: HashDigest
    destination_ref: ArtifactReference
    destination_sha256: HashDigest
    policy_sha256: HashDigest
    gate_context: GateContext
    workspace_observation_sha256: HashDigest
    release_intent_sha256: HashDigest
    created_at: str
    authority_effect: str = "none"


@dataclass(frozen=True, slots=True)
class ReleaseCandidateVerificationInputs:
    """Exact current evidence required by every release consumer."""

    workspace_id: str
    channel_id: str
    concept_id: str
    episode_id: str
    created_at: str
    quality_origin_evaluated_at: str
    final_media: MediaSubject
    metadata_ref: ArtifactReference
    subtitle_accessibility_refs: tuple[ArtifactReference, ...]
    thumbnail: MediaSubject
    quality_bundle_ref: ArtifactReference
    quality_bundle: QualityBundle
    candidate_decision_ref: ArtifactReference
    candidate_decision: CandidateDecision
    candidate_verification: CandidateDecisionVerificationInputs
    destination_ref: ArtifactReference
    destination: DestinationBinding
    policy: QualityPolicy
    current_context: GateContext
    verified_at: datetime
    quality_resolver: CurrentQualityEvidenceResolver
    evaluation_verifier: CurrentQualityEvaluationVerifier


@dataclass(frozen=True, slots=True)
class ReleaseAuthorityEvidence:
    request: ActionAuthorityRequest
    risk: ActionRiskAssessment
    decision: AuthorityDecision
    receipt: AuthorityVerificationReceipt | None


@dataclass(frozen=True, slots=True)
class ReleaseAssessment:
    artifact_version: str
    assessment_id: OpaqueId
    assessment_sha256: HashDigest
    release_candidate_ref: ArtifactReference
    release_candidate_sha256: HashDigest
    gate_context: GateContext
    destination_sha256: HashDigest
    evaluated_at: str
    authority_request_sha256: HashDigest | None
    risk_assessment_sha256: HashDigest | None
    authority_decision_sha256: HashDigest | None
    authority_receipt_sha256: HashDigest | None
    approval_request: ApprovalRequest | None
    status: ReleaseAssessmentStatus
    reason_codes: tuple[str, ...]
    required_independent_humans: int
    publish_performed: bool = False
    authority_effect: str = "none"


__all__ = [
    "DestinationBinding",
    "ReleaseAssessment",
    "ReleaseAssessmentStatus",
    "ReleaseAuthorityEvidence",
    "ReleaseCandidate",
    "ReleaseCandidateVerificationInputs",
    "ReleaseContractError",
    "ReleaseVisibility",
]
