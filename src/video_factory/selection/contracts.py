"""Confidence-bound W05 candidate-selection contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

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
    confidence_receipt_ref: ArtifactReference


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
    confidence_receipt_ref: ArtifactReference


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
    workspace_id: OpaqueId
    channel_id: OpaqueId
    concept_id: OpaqueId
    episode_id: OpaqueId
    quality_bundle_ref: ArtifactReference
    quality_bundle_sha256: HashDigest
    policy_sha256: HashDigest
    gate_context: GateContext
    evaluated_at: str
    selection_input_sha256: HashDigest
    authority_request_sha256: HashDigest | None
    authority_risk_sha256: HashDigest | None
    authority_decision_sha256: HashDigest | None
    authority_receipt_sha256: HashDigest | None
    shots: tuple[ShotCandidateDecision, ...]
    status: CandidateDecisionStatus
    reason_codes: tuple[str, ...]
    authority_effect: str = "none"


@dataclass(frozen=True, slots=True)
class CandidateDecisionVerificationInputs:
    """Ephemeral evidence required to cleanly re-verify a persisted decision."""

    workspace_id: str
    channel_id: str
    concept_id: str
    episode_id: str
    quality_origin_evaluated_at: str
    quality_bundle_ref: ArtifactReference
    quality_bundle: QualityBundle
    candidate_sets: tuple[ShotCandidateSet, ...]
    policy: QualityPolicy
    current_context: GateContext
    verified_at: datetime
    origin_evaluated_at: datetime
    origin_authority: InitialAuthorityEvidence | None
    origin_authority_ledger: TrustedAuthorizationLedger | None
    authority: InitialAuthorityEvidence | None
    authority_ledger: TrustedAuthorizationLedger | None
    quality_resolver: CurrentQualityEvidenceResolver | None
    evaluation_verifier: CurrentQualityEvaluationVerifier | None
    confidence_verifier: CurrentCandidateConfidenceVerifier | None
    authority_references: tuple[ArtifactReference, ...] = ()


class CurrentCandidateConfidenceVerifier(Protocol):
    """Trusted port that authenticates one exact current confidence receipt."""

    def verify_current(
        self,
        option: CandidateOption,
        *,
        gate_context: GateContext,
        policy: QualityPolicy,
        evaluated_at: datetime,
    ) -> CandidateOption | None: ...


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
    "CurrentCandidateConfidenceVerifier",
    "LegacyCandidateSelectionProjection",
    "RankedCandidate",
    "SelectionContractError",
    "ShotCandidateDecision",
    "ShotCandidateSet",
]
