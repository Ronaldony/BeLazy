"""Immutable contracts for the managed mutation plane.

The contracts describe intent, plans, observations, receipts, immutable
revisions, drift, and emergency authority.  They carry no filesystem handles
and perform no side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from video_factory.domain import (
    ArtifactReference,
    HashDigest,
    IdempotencyKey,
    OpaqueId,
    RelativeArtifactPath,
)


class MutationKind(StrEnum):
    CREATE = "create"
    REPLACE = "replace"
    DELETE = "delete"
    MOVE = "move"


class MutationRiskTier(StrEnum):
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    R4 = "R4"


class WorkspaceTrustState(StrEnum):
    TRUSTED = "TRUSTED"
    UNTRUSTED = "UNTRUSTED"


class WorkspaceRevisionOrigin(StrEnum):
    RECONCILED_BASELINE = "reconciled_baseline"
    MANAGED_MUTATION = "managed_mutation"


class PathNodeKind(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    REPARSE = "reparse"


class MutationReceiptStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    PARTIALLY_APPLIED = "PARTIALLY_APPLIED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"
    RECONCILED = "RECONCILED"


class OperationOutcome(StrEnum):
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class DriftKind(StrEnum):
    ADDED = "added"
    MISSING = "missing"
    MODIFIED = "modified"
    TYPE_CHANGED = "type_changed"
    PATH_ALIAS = "path_alias"
    LINK_OR_REPARSE = "link_or_reparse"
    MANIFEST_MISMATCH = "manifest_mismatch"


@dataclass(frozen=True, slots=True)
class ContentObject:
    object_id: OpaqueId
    exact_sha256: HashDigest
    byte_length: int


@dataclass(frozen=True, slots=True)
class ContentObjectObservation:
    object_id: OpaqueId
    exact_sha256: HashDigest
    byte_length: int
    resolver_evidence: ArtifactReference


@dataclass(frozen=True, slots=True)
class ExpectedBefore:
    exists: bool
    exact_sha256: HashDigest | None


@dataclass(frozen=True, slots=True)
class MutationOperationIntent:
    kind: MutationKind
    path: RelativeArtifactPath
    expected_before: ExpectedBefore
    new_content: ContentObject | None = None
    destination_path: RelativeArtifactPath | None = None


@dataclass(frozen=True, slots=True)
class ChangeRequest:
    request_id: OpaqueId
    workspace_id: OpaqueId
    requester_id: OpaqueId
    requested_at: str
    before_revision_id: OpaqueId
    before_manifest_sha256: HashDigest
    idempotency_key: IdempotencyKey
    risk_tier: MutationRiskTier
    operations: tuple[MutationOperationIntent, ...]


@dataclass(frozen=True, slots=True)
class PlannedMutationOperation:
    operation_id: OpaqueId
    kind: MutationKind
    path: RelativeArtifactPath
    expected_before: ExpectedBefore
    new_content: ContentObject | None
    destination_path: RelativeArtifactPath | None
    rollback_strategy: str


@dataclass(frozen=True, slots=True)
class SemanticDiffEntry:
    operation_id: OpaqueId
    kind: MutationKind
    path: RelativeArtifactPath
    destination_path: RelativeArtifactPath | None
    before_sha256: HashDigest | None
    after_sha256: HashDigest | None


@dataclass(frozen=True, slots=True)
class MutationPlan:
    plan_id: OpaqueId
    plan_sha256: HashDigest
    request_id: OpaqueId
    change_request_sha256: HashDigest
    workspace_id: OpaqueId
    before_revision_id: OpaqueId
    before_workspace_revision_sha256: HashDigest
    before_manifest_sha256: HashDigest
    policy_bundle_sha256: HashDigest
    risk_policy_version: str
    idempotency_key: IdempotencyKey
    risk_tier: MutationRiskTier
    operations: tuple[PlannedMutationOperation, ...]
    semantic_diff: tuple[SemanticDiffEntry, ...]
    # Added additively after mutation-plan/1.0 was introduced.  Legacy plans
    # remain parseable, but an unbound requester can never cross the W04 guard.
    requester_id: OpaqueId | None = None


@dataclass(frozen=True, slots=True)
class PathObservation:
    path: RelativeArtifactPath
    node_kind: PathNodeKind
    exact_sha256: HashDigest | None
    byte_length: int | None


@dataclass(frozen=True, slots=True)
class WorkspaceObservation:
    workspace_id: OpaqueId
    revision_id: OpaqueId
    manifest_sha256: HashDigest
    trust_state: WorkspaceTrustState
    complete: bool
    entries: tuple[PathObservation, ...]


@dataclass(frozen=True, slots=True)
class OperationResult:
    operation_id: OpaqueId
    kind: MutationKind
    path: RelativeArtifactPath
    destination_path: RelativeArtifactPath | None
    outcome: OperationOutcome
    before_sha256: HashDigest | None
    after_sha256: HashDigest | None
    after_byte_length: int | None
    reason_code: str


@dataclass(frozen=True, slots=True)
class MutationReceipt:
    receipt_id: OpaqueId
    plan_id: OpaqueId
    plan_sha256: HashDigest
    workspace_id: OpaqueId
    idempotency_key: IdempotencyKey
    executor_identity: OpaqueId
    execution_authorization_id: OpaqueId
    execution_authorization_sha256: HashDigest
    before_workspace_observation_sha256: HashDigest
    after_workspace_observation_sha256: HashDigest
    idempotency_reservation: ArtifactReference
    journal_record: ArtifactReference
    started_at: str
    completed_at: str
    status: MutationReceiptStatus
    before_manifest_sha256: HashDigest
    after_manifest_sha256: HashDigest
    operation_results: tuple[OperationResult, ...]
    rollback_or_reconciliation_required: bool
    rollback_or_reconciliation_plan: ArtifactReference | None


@dataclass(frozen=True, slots=True)
class RevisionEntry:
    path: RelativeArtifactPath
    revision_ordinal: int
    content_sha256: HashDigest | None
    byte_length: int | None
    tombstone: bool
    source_operation_id: OpaqueId


@dataclass(frozen=True, slots=True)
class WorkspaceRevision:
    revision_id: OpaqueId
    workspace_id: OpaqueId
    origin: WorkspaceRevisionOrigin
    parent_revision_id: OpaqueId | None
    reconciliation_evidence: ArtifactReference | None
    manifest_sha256: HashDigest
    created_at: str
    plan_id: OpaqueId
    receipt_id: OpaqueId
    trust_state: WorkspaceTrustState
    entries: tuple[RevisionEntry, ...]


@dataclass(frozen=True, slots=True)
class DriftFinding:
    path: RelativeArtifactPath
    kind: DriftKind
    expected_sha256: HashDigest | None
    observed_sha256: HashDigest | None
    reason_code: str


@dataclass(frozen=True, slots=True)
class DriftReport:
    report_id: OpaqueId
    workspace_id: OpaqueId
    expected_revision_id: OpaqueId
    expected_manifest_sha256: HashDigest
    observed_manifest_sha256: HashDigest
    detected_at: str
    trust_state: WorkspaceTrustState
    findings: tuple[DriftFinding, ...]
    generation_blocked: bool
    publish_blocked: bool
    dependent_plans_invalidated: bool
    authority_invalidated: bool
    qc_invalidated: bool
    reconciliation_required: bool


@dataclass(frozen=True, slots=True)
class AuthenticatedHumanApproval:
    principal_id: OpaqueId
    break_glass_request_sha256: HashDigest
    authentication_evidence: ArtifactReference
    approval_record: ArtifactReference
    approved_at: str


@dataclass(frozen=True, slots=True)
class BreakGlassAuthorization:
    authorization_id: OpaqueId
    plan_sha256: HashDigest
    workspace_id: OpaqueId
    before_revision_id: OpaqueId
    before_manifest_sha256: HashDigest
    issued_at: str
    expires_at: str
    exact_paths: tuple[RelativeArtifactPath, ...]
    operation_kinds: frozenset[MutationKind]
    approvals: tuple[AuthenticatedHumanApproval, ...]
    pre_change_snapshot: ArtifactReference
    incident_id: OpaqueId
    incident_record: ArtifactReference
    audit_record: ArtifactReference
    session_id: OpaqueId
    executor_identity: OpaqueId
    standing_grant: bool = False
    risk_tier: MutationRiskTier = MutationRiskTier.R4
    reconciliation_required: bool = True
    post_change_validation_required: bool = True


@dataclass(frozen=True, slots=True)
class IdempotencyReservation:
    idempotency_key: IdempotencyKey
    plan_sha256: HashDigest
    workspace_observation_sha256: HashDigest
    reservation_record: ArtifactReference
    newly_reserved: bool
    existing_receipt_id: OpaqueId | None


@dataclass(frozen=True, slots=True)
class BreakGlassEvidenceVerification:
    """Trusted verifier output for the snapshot, incident, and audit records."""

    break_glass_request_sha256: HashDigest
    snapshot_verification: ArtifactReference
    incident_verification: ArtifactReference
    audit_verification: ArtifactReference


@dataclass(frozen=True, slots=True)
class MutationExecutionAuthorization:
    authorization_id: OpaqueId
    plan_id: OpaqueId
    plan_sha256: HashDigest
    workspace_id: OpaqueId
    revision_id: OpaqueId
    workspace_revision_sha256: HashDigest
    manifest_sha256: HashDigest
    workspace_observation_sha256: HashDigest
    idempotency_key: IdempotencyKey
    idempotency_reservation: ArtifactReference
    content_observation_sha256: HashDigest
    content_observations: tuple[ContentObjectObservation, ...]
    content_verifications: tuple[ArtifactReference, ...]
    service_identity: OpaqueId
    evaluated_at: str
    gate_context_sha256: HashDigest
    authority_decision: ArtifactReference | None
    break_glass_authorization_id: OpaqueId | None
    break_glass_authorization_sha256: HashDigest | None
    break_glass_request_sha256: HashDigest | None
    human_approval_verifications: tuple[ArtifactReference, ...]
    break_glass_evidence_verifications: tuple[ArtifactReference, ...]
