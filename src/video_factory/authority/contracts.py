"""Authority artifacts and trusted verification ports.

Raw grants and approval documents are deliberately named *unverified*.  Only a
decision produced through a trusted ledger port can cross the authority seam.
Concrete ledger persistence, signatures and budget settlement remain runtime
responsibilities (W06).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from video_factory.approvals import GateContext
from video_factory.domain import ArtifactReference, HashDigest, IdempotencyKey, OpaqueId
from video_factory.workflow import (
    ActionRisk,
    AssuranceProfile,
    AuthorityRequirement,
    AutonomyProfile,
    ExecutableProductionPlan,
    WorkflowEvaluation,
)


class AuthorityContractError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class AuthorityDecisionStatus(StrEnum):
    AUTHORIZED = "authorized"
    HUMAN_APPROVAL_REQUIRED = "human_approval_required"
    DENIED = "denied"


class AuthoritySource(StrEnum):
    POLICY = "policy"
    STANDING_GRANT = "standing_grant"
    ONE_SHOT_HUMAN = "one_shot_human"
    RELEASE_CAMPAIGN = "release_campaign"
    DUAL_HUMAN = "dual_human"
    NONE = "none"


class VerificationPurpose(StrEnum):
    INITIAL_DECISION = "initial_decision"
    DISPATCH = "dispatch"
    RECONCILE = "reconcile"
    MUTATION = "mutation"


class LedgerRecordState(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"
    SUPERSEDED = "superseded"
    UNKNOWN = "unknown"


class HardEscalationState(StrEnum):
    """Closed tri-state for one target-owned hard-escalation trigger."""

    CLEAR = "clear"
    TRIGGERED = "triggered"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ProfileSelection:
    assurance: AssuranceProfile
    autonomy: AutonomyProfile


@dataclass(frozen=True, slots=True)
class OutputScope:
    path_prefix: str
    artifact_versions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AuthorityScope:
    workspace_id: OpaqueId
    channel_id: OpaqueId
    concept_id: OpaqueId
    episode_id: OpaqueId
    provider_id: OpaqueId | None
    model_id: OpaqueId | None
    destination: str | None
    cost_minor_units: int
    currency: str
    candidate_count: int
    retry_index: int
    input_artifacts: tuple[ArtifactReference, ...]
    allowed_outputs: tuple[OutputScope, ...]


@dataclass(frozen=True, slots=True)
class PrincipalSignatureVerification:
    """One authenticated human principal bound to one signature check."""

    principal_id: OpaqueId
    signature_verification_ref: ArtifactReference


@dataclass(frozen=True, slots=True)
class HardEscalationFact:
    """One material policy fact plus exact evidence supplied for verification.

    The shape is structural only.  A trusted ledger/runtime must authenticate
    the referenced evidence; constructing this value never creates authority.
    """

    trigger: str
    state: HardEscalationState
    evidence_refs: tuple[ArtifactReference, ...]


@dataclass(frozen=True, slots=True)
class ActionAuthorityRequest:
    request_id: OpaqueId
    request_sha256: HashDigest
    request_envelope_sha256: HashDigest
    idempotency_key: IdempotencyKey
    requester_principal_id: OpaqueId
    action_id: OpaqueId
    capability_id: OpaqueId
    executable_plan_sha256: HashDigest
    action_risk: ActionRisk
    authority_requirement: AuthorityRequirement
    side_effect: bool
    plan: ExecutableProductionPlan | None
    workflow_evaluation: WorkflowEvaluation | None
    gate_context: GateContext
    profiles: ProfileSelection
    scope: AuthorityScope
    hard_escalation_facts: tuple[HardEscalationFact, ...]


@dataclass(frozen=True, slots=True)
class PolicyEnforcementRule:
    policy_path: str
    owner: str
    enforcement_phase: str
    reason_code: str
    positive_test_id: str
    negative_test_id: str


@dataclass(frozen=True, slots=True)
class PolicyBundle:
    artifact_version: str
    bundle_id: OpaqueId
    bundle_sha256: HashDigest
    policy_version: str
    classifier_version: str
    governance_policy_sha256: HashDigest
    default_decision: AuthorityDecisionStatus
    unknown_state_fail_closed: bool
    self_approval_forbidden: bool
    release_campaign_enabled: bool
    hard_escalation_triggers: tuple[str, ...]
    action_risk_by_action: tuple[tuple[OpaqueId, ActionRisk], ...]
    enforcement_matrix: tuple[PolicyEnforcementRule, ...]


@dataclass(frozen=True, slots=True)
class ActionRiskAssessment:
    artifact_version: str
    assessment_id: OpaqueId
    assessment_sha256: HashDigest
    classifier_version: str
    policy_bundle_sha256: HashDigest
    action_request_sha256: HashDigest
    effective_risk: ActionRisk
    reason_codes: tuple[str, ...]
    supported: bool


@dataclass(frozen=True, slots=True)
class UnverifiedStandingAuthorization:
    artifact_version: str
    authorization_id: OpaqueId
    authorization_sha256: HashDigest
    gate_context_sha256: HashDigest
    capability_ids: tuple[OpaqueId, ...]
    workspace_ids: tuple[OpaqueId, ...]
    channel_ids: tuple[OpaqueId, ...]
    concept_ids: tuple[OpaqueId, ...]
    episode_ids: tuple[OpaqueId, ...]
    provider_ids: tuple[OpaqueId, ...]
    model_ids: tuple[OpaqueId, ...]
    destinations: tuple[str, ...]
    max_cost_per_run_minor: int
    max_cost_per_day_minor: int
    currency: str
    max_candidates: int
    max_retries: int
    allowed_risks: tuple[ActionRisk, ...]
    minimum_assurance: AssuranceProfile
    maximum_autonomy: AutonomyProfile
    exact_input_artifacts: tuple[ArtifactReference, ...]
    allowed_outputs: tuple[OutputScope, ...]
    valid_from: str
    expires_at: str
    ledger_record: ArtifactReference
    signature_verification_refs: tuple[ArtifactReference, ...]
    authority_effect: str = "none"


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    artifact_version: str
    approval_request_id: OpaqueId
    approval_request_sha256: HashDigest
    action_request_sha256: HashDigest
    gate_context_sha256: HashDigest
    risk_assessment_sha256: HashDigest
    required_authority: AuthorityRequirement
    required_independent_humans: int
    reason_codes: tuple[str, ...]
    material_diff_sha256: HashDigest
    safe_default: AuthorityDecisionStatus
    creates_authority: bool = False
    authority_effect: str = "none"


@dataclass(frozen=True, slots=True)
class AuthorityVerificationReceipt:
    artifact_version: str
    receipt_id: OpaqueId
    receipt_sha256: HashDigest
    purpose: VerificationPurpose
    action_request_sha256: HashDigest
    authority_decision_sha256: HashDigest | None
    gate_context_sha256: HashDigest
    risk_assessment_sha256: HashDigest
    workflow_evaluation_sha256: HashDigest | None
    workflow_evaluation_verification_ref: ArtifactReference | None
    authority_source: AuthoritySource
    ledger_state: LedgerRecordState
    ledger_head_sha256: HashDigest
    ledger_entry: ArtifactReference
    requester_principal_id: OpaqueId
    requester_authentication_ref: ArtifactReference
    grant_sha256: HashDigest | None
    principal_verifications: tuple[PrincipalSignatureVerification, ...]
    signature_verification_refs: tuple[ArtifactReference, ...]
    revocation_checked_at: str
    kill_switch_clear: bool
    reserved_cost_minor_units: int
    currency: str
    reserved_candidates: int
    retry_index: int
    idempotency_key: IdempotencyKey
    workspace_id: OpaqueId
    workspace_observation_sha256: HashDigest | None
    adapter_id: OpaqueId | None
    service_identity: OpaqueId | None
    evaluated_at: str
    valid_until: str


@dataclass(frozen=True, slots=True)
class AuthorityDecision:
    artifact_version: str
    decision_id: OpaqueId
    decision_sha256: HashDigest
    action_request_sha256: HashDigest
    gate_context_sha256: HashDigest
    risk_assessment_sha256: HashDigest
    effective_risk: ActionRisk
    required_authority: AuthorityRequirement
    status: AuthorityDecisionStatus
    source: AuthoritySource
    reason_codes: tuple[str, ...]
    matched_limit_sha256: HashDigest | None
    authority_basis_sha256: HashDigest | None
    verification_receipt_id: OpaqueId | None
    verification_receipt_sha256: HashDigest | None
    evaluated_at: str
    valid_until: str | None
    predispatch_required: bool
    authority_effect: str


@dataclass(frozen=True, slots=True)
class VerifiedAuthorityDecision:
    decision: AuthorityDecision
    receipt: AuthorityVerificationReceipt
    request_sha256: HashDigest
    purpose: VerificationPurpose


class TrustedAuthorizationLedger(Protocol):
    """Port implemented by a trusted runtime, never by a raw document.

    For workflow requests, implementations independently resolve the current
    observation/evidence graph and issue the workflow-evaluation verification
    reference only when its clean evaluation digest exactly matches the
    request.  Deriving that proof from caller-supplied fields alone violates
    this port contract.
    """

    def verify_current(
        self,
        request: ActionAuthorityRequest,
        risk: ActionRiskAssessment,
        presented_grant: UnverifiedStandingAuthorization | None,
        authority_references: tuple[ArtifactReference, ...],
        *,
        current_context: GateContext,
        evaluated_at: datetime,
    ) -> AuthorityVerificationReceipt | None: ...

    def revalidate_and_reserve_current(
        self,
        decision: AuthorityDecision,
        request: ActionAuthorityRequest,
        *,
        current_context: GateContext,
        workspace_observation_sha256: HashDigest,
        adapter_id: OpaqueId,
        service_identity: OpaqueId,
        evaluated_at: datetime,
        purpose: VerificationPurpose,
    ) -> AuthorityVerificationReceipt | None: ...
