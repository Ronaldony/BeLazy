"""Confidence-bound W05 candidate-selection contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from video_factory.approvals import GateContext
from video_factory.authority import TrustedAuthorizationLedger
from video_factory.domain import ArtifactReference, HashDigest, OpaqueId
from video_factory.quality import (
    CurrentQualityEvaluationVerifier,
    CurrentQualityEvidenceResolver,
    InitialAuthorityEvidence,
    QualityBundle,
    QualityPolicy,
)
from video_factory.quality.contracts import MediaSubject


class SelectionContractError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class CandidateDecisionStatus(StrEnum):
    AUTO_SELECTED = "auto_selected"
    ESCALATION_REQUIRED = "escalation_required"
    DENIED = "denied"


@dataclass(frozen=True, slots=True)
class CandidateOption:
    subject: MediaSubject
    adapter_id: OpaqueId
    confidence_bps: int


@dataclass(frozen=True, slots=True)
class ShotCandidateSet:
    shot_id: OpaqueId
    candidates: tuple[CandidateOption, ...]


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    subject: MediaSubject
    adapter_id: OpaqueId
    quality_score_bps: int
    confidence_bps: int


@dataclass(frozen=True, slots=True)
class ShotCandidateDecision:
    shot_id: OpaqueId
    status: CandidateDecisionStatus
    ranked_candidates: tuple[RankedCandidate, ...]
    selected_subject: MediaSubject | None
    margin_to_second_bps: int
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CandidateDecision:
    artifact_version: str
    decision_id: OpaqueId
    decision_sha256: HashDigest
    episode_id: OpaqueId
    quality_bundle_ref: ArtifactReference
    quality_bundle_sha256: HashDigest
    policy_sha256: HashDigest
    gate_context: GateContext
    evaluated_at: str
    selection_input_sha256: HashDigest
    authority_request_sha256: HashDigest | None
    authority_decision_sha256: HashDigest | None
    authority_receipt_sha256: HashDigest | None
    shots: tuple[ShotCandidateDecision, ...]
    status: CandidateDecisionStatus
    reason_codes: tuple[str, ...]
    authority_effect: str = "none"


@dataclass(frozen=True, slots=True)
class CandidateDecisionVerificationInputs:
    """Ephemeral evidence required to cleanly re-verify a persisted decision."""

    quality_bundle_ref: ArtifactReference
    quality_bundle: QualityBundle
    candidate_sets: tuple[ShotCandidateSet, ...]
    policy: QualityPolicy
    current_context: GateContext
    evaluated_at: datetime
    authority: InitialAuthorityEvidence | None
    authority_ledger: TrustedAuthorizationLedger | None
    quality_resolver: CurrentQualityEvidenceResolver | None
    evaluation_verifier: CurrentQualityEvaluationVerifier | None
    authority_references: tuple[ArtifactReference, ...] = ()


@dataclass(frozen=True, slots=True)
class LegacyCandidateSelectionProjection:
    source_candidate_decision_sha256: HashDigest
    candidate_ranking: dict[str, object]
    edit_manifest: None = None
    current_eligible: bool = False
    authority_effect: str = "none"


__all__ = [
    "CandidateDecision",
    "CandidateDecisionStatus",
    "CandidateDecisionVerificationInputs",
    "CandidateOption",
    "LegacyCandidateSelectionProjection",
    "RankedCandidate",
    "SelectionContractError",
    "ShotCandidateDecision",
    "ShotCandidateSet",
]
