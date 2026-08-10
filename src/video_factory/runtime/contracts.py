"""Pure contracts shared with the concrete W06 runtime boundary.

The :mod:`video_factory` package remains plan-only.  These values describe
durable runtime evidence, but they never open a database, mutate a workspace,
resolve credentials, or call an external adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from video_factory.domain import (
    ArtifactReference,
    HashDigest,
    IdempotencyKey,
    OpaqueId,
)


EXECUTION_INTENT_VERSION = "execution-intent/1.0"
JOURNAL_EVENT_VERSION = "execution-journal-event/1.0"
EXECUTION_RECEIPT_VERSION = "execution-receipt/1.0"
PROJECTION_PARITY_VERSION = "projection-parity-receipt/1.0"
MIGRATION_CUTOVER_VERSION = "migration-cutover-state/1.0"
PUBLICATION_INTENT_VERSION = "publication-intent/1.0"
PUBLICATION_RECEIPT_VERSION = "publication-receipt/1.0"


class RuntimeActionKind(StrEnum):
    EXECUTOR = "executor"
    MUTATION = "mutation"
    PUBLICATION = "publication"
    MIGRATION = "migration"


class JournalState(StrEnum):
    PLANNED = "planned"
    AUTHORIZED = "authorized"
    RESERVED = "reserved"
    DISPATCHING = "dispatching"
    DISPATCHED = "dispatched"
    RECONCILING = "reconciling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"
    UNCERTAIN = "uncertain"
    RECONCILED = "reconciled"


class MigrationMode(StrEnum):
    LEGACY_ONLY = "legacy_only"
    DUAL_READ_COMPARE = "dual_read_compare"
    PROJECTION_READ_ONLY = "projection_read_only"
    ROLLED_BACK = "rolled_back"


class PublicationStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    RECONCILED = "reconciled"


@dataclass(frozen=True, slots=True)
class ExecutionIntent:
    artifact_version: str
    intent_id: OpaqueId
    intent_sha256: HashDigest
    request_id: OpaqueId
    request_sha256: HashDigest
    idempotency_key: IdempotencyKey
    action_kind: RuntimeActionKind
    action_id: OpaqueId
    planned_effect_ids: tuple[OpaqueId, ...]
    workspace_id: OpaqueId
    plan_sha256: HashDigest
    gate_context_sha256: HashDigest
    workspace_observation_sha256: HashDigest
    authority_decision_sha256: HashDigest
    authority_receipt_sha256: HashDigest | None
    service_identity: OpaqueId
    service_identity_attestation: ArtifactReference
    credential_handle_id: OpaqueId | None
    credential_verification: ArtifactReference | None
    destination: str | None
    created_at: str


@dataclass(frozen=True, slots=True)
class ExecutionJournalEvent:
    artifact_version: str
    event_id: OpaqueId
    event_sha256: HashDigest
    journal_id: OpaqueId
    sequence: int
    previous_event_sha256: HashDigest | None
    intent_sha256: HashDigest
    authority_receipt_sha256: HashDigest | None
    workspace_observation_sha256: HashDigest | None
    reservation_sha256: HashDigest | None
    state: JournalState
    operation_id: OpaqueId | None
    occurred_at: str
    reason_codes: tuple[str, ...]
    external_reference_id: OpaqueId | None
    may_have_started: bool


@dataclass(frozen=True, slots=True)
class ExecutionReceipt:
    artifact_version: str
    receipt_id: OpaqueId
    receipt_sha256: HashDigest
    intent_sha256: HashDigest
    journal_id: OpaqueId
    journal_head_sha256: HashDigest
    final_state: JournalState
    started_at: str
    completed_at: str
    output_refs: tuple[ArtifactReference, ...]
    measured_cost_minor_units: int | None
    currency: str | None
    before_workspace_observation_sha256: HashDigest
    after_workspace_observation_sha256: HashDigest | None
    authority_receipt_sha256: HashDigest | None
    settlement_record: ArtifactReference | None
    reconciliation_record: ArtifactReference | None
    reason_codes: tuple[str, ...]
    authority_effect: str = "none"


@dataclass(frozen=True, slots=True)
class PublicationIntent:
    artifact_version: str
    intent_id: OpaqueId
    intent_sha256: HashDigest
    request_id: OpaqueId
    idempotency_key: IdempotencyKey
    release_candidate: ArtifactReference
    release_candidate_sha256: HashDigest
    release_assessment: ArtifactReference
    release_assessment_sha256: HashDigest
    destination: ArtifactReference
    destination_sha256: HashDigest
    workspace_id: OpaqueId
    channel_id: OpaqueId
    concept_id: OpaqueId
    episode_id: OpaqueId
    gate_context_sha256: HashDigest
    workspace_observation_sha256: HashDigest
    publisher_id: OpaqueId
    publisher_variant_id: OpaqueId
    destination_id: OpaqueId
    cost_minor_units: int
    currency: str
    retry_index: int
    created_at: str
    authority_effect: str = "none"


@dataclass(frozen=True, slots=True)
class PublicationReceipt:
    artifact_version: str
    receipt_id: OpaqueId
    receipt_sha256: HashDigest
    publication_intent_sha256: HashDigest
    journal_id: OpaqueId
    journal_head_sha256: HashDigest
    release_candidate: ArtifactReference
    release_assessment: ArtifactReference
    destination: ArtifactReference
    workspace_before_verifier_record: ArtifactReference
    workspace_after_verifier_record: ArtifactReference | None
    action_request_sha256: HashDigest
    authority_decision_sha256: HashDigest
    authority_receipt_sha256: HashDigest
    service_identity: OpaqueId
    external_reference_id: OpaqueId | None
    external_request_id: str | None
    external_session_id: str | None
    started_at: str
    completed_at: str
    status: PublicationStatus
    published: bool | None
    measured_cost_minor_units: int | None
    currency: str | None
    settlement_record: ArtifactReference | None
    reconciliation_record: ArtifactReference | None
    reason_codes: tuple[str, ...]
    authority_effect: str = "none"


@dataclass(frozen=True, slots=True)
class ProjectionParityReceipt:
    artifact_version: str
    receipt_id: OpaqueId
    receipt_sha256: HashDigest
    consumer_id: OpaqueId
    view_kind: OpaqueId
    legacy_artifact: ArtifactReference
    projection_artifact: ArtifactReference
    source_blueprint_sha256: HashDigest
    projection_sha256: HashDigest
    legacy_normalized_sha256: HashDigest
    projection_normalized_sha256: HashDigest
    normalizer_id: OpaqueId
    normalizer_version: str
    normalizer_sha256: HashDigest
    verifier_record: ArtifactReference
    policy_bundle_sha256: HashDigest
    evaluated_at: str
    parity_pass: bool
    difference_count: int
    authority_effect: str = "none"


@dataclass(frozen=True, slots=True)
class MigrationCutoverState:
    artifact_version: str
    state_id: OpaqueId
    state_sha256: HashDigest
    migration_id: OpaqueId
    consumer_id: OpaqueId
    view_kind: OpaqueId
    generation: int
    mode: MigrationMode
    previous_state_sha256: HashDigest | None
    legacy_artifact: ArtifactReference
    parity_receipts: tuple[ArtifactReference, ...]
    feature_flag_sha256: HashDigest
    activation_record: ArtifactReference
    rollback_record: ArtifactReference | None
    activated_at: str
    rollback_required: bool
    read_only: bool = True
    projections_are_authority: bool = False
    authority_effect: str = "none"


__all__ = [
    "EXECUTION_INTENT_VERSION",
    "EXECUTION_RECEIPT_VERSION",
    "JOURNAL_EVENT_VERSION",
    "MIGRATION_CUTOVER_VERSION",
    "PROJECTION_PARITY_VERSION",
    "PUBLICATION_INTENT_VERSION",
    "PUBLICATION_RECEIPT_VERSION",
    "ExecutionIntent",
    "ExecutionJournalEvent",
    "ExecutionReceipt",
    "JournalState",
    "MigrationCutoverState",
    "MigrationMode",
    "PublicationIntent",
    "PublicationReceipt",
    "PublicationStatus",
    "ProjectionParityReceipt",
    "RuntimeActionKind",
]
