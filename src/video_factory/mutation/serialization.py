"""Strict typed serialization for managed-mutation artifacts.

Bytes are parsed through the bounded JSON boundary, mappings are validated
against the packaged schema registry, and material cross-field identities are
recomputed before a typed contract is returned.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeAlias

from video_factory.artifacts import validate_artifact_mapping
from video_factory.config.canonical import canonical_sha256
from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    HashDigest,
    IdempotencyKey,
    OpaqueId,
    RelativeArtifactPath,
)
from video_factory.json_boundary import (
    JsonInputError,
    parse_json_bytes,
    require_json_object,
    validate_json_mapping,
)

from .contracts import (
    AuthenticatedHumanApproval,
    BreakGlassAuthorization,
    ChangeRequest,
    ContentObject,
    DriftFinding,
    DriftKind,
    DriftReport,
    ExpectedBefore,
    MutationKind,
    MutationOperationIntent,
    MutationPlan,
    MutationReceipt,
    MutationReceiptStatus,
    MutationRiskTier,
    OperationOutcome,
    OperationResult,
    PlannedMutationOperation,
    RevisionEntry,
    SemanticDiffEntry,
    WorkspaceRevision,
    WorkspaceRevisionOrigin,
    WorkspaceTrustState,
)
from .paths import MutationPathError, require_collision_free, require_managed_path
from .planner import (
    MutationPlanError,
    break_glass_authorization_to_mapping,
    change_request_to_mapping,
    drift_report_to_mapping,
    mutation_plan_to_mapping,
    mutation_receipt_to_mapping,
    validate_mutation_receipt,
    validate_mutation_plan,
    validate_drift_report,
    validate_workspace_revision,
    workspace_revision_to_mapping,
)


MutationArtifact: TypeAlias = (
    ChangeRequest
    | MutationPlan
    | MutationReceipt
    | WorkspaceRevision
    | DriftReport
    | BreakGlassAuthorization
)


class MutationSerializationError(ValueError):
    """A mutation document failed strict parsing, schema, or typed validation."""

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
        raise MutationSerializationError(error.code.value, str(error)) from error
    if normalized.get("artifact_version") != expected_version:
        raise MutationSerializationError(
            "mutation.serialization.version",
            f"expected {expected_version!r}",
        )
    result = validate_artifact_mapping(normalized)
    if not result.ok:
        raise MutationSerializationError(
            "mutation.serialization.schema",
            "; ".join(result.error_texts),
        )
    return normalized


def _reference(value: object) -> ArtifactReference:
    assert isinstance(value, Mapping)
    return ArtifactReference(
        path=RelativeArtifactPath(str(value["path"])),
        sha256=HashDigest(str(value["sha256"])),
        artifact_version=ArtifactVersion(str(value["artifact_version"])),
    )


def _content(value: object) -> ContentObject | None:
    if value is None:
        return None
    assert isinstance(value, Mapping)
    return ContentObject(
        object_id=OpaqueId(str(value["object_id"])),
        exact_sha256=HashDigest(str(value["exact_sha256"])),
        byte_length=int(value["byte_length"]),
    )


def _expected(value: object) -> ExpectedBefore:
    assert isinstance(value, Mapping)
    digest = value["exact_sha256"]
    return ExpectedBefore(
        exists=bool(value["exists"]),
        exact_sha256=HashDigest(str(digest)) if digest is not None else None,
    )


def _intent(value: object) -> MutationOperationIntent:
    assert isinstance(value, Mapping)
    destination = value["destination_path"]
    return MutationOperationIntent(
        kind=MutationKind(str(value["kind"])),
        path=RelativeArtifactPath(str(value["path"])),
        expected_before=_expected(value["expected_before"]),
        new_content=_content(value["new_content"]),
        destination_path=(
            RelativeArtifactPath(str(destination)) if destination is not None else None
        ),
    )


def _planned(value: object) -> PlannedMutationOperation:
    assert isinstance(value, Mapping)
    intent = _intent(value)
    return PlannedMutationOperation(
        operation_id=OpaqueId(str(value["operation_id"])),
        kind=intent.kind,
        path=intent.path,
        expected_before=intent.expected_before,
        new_content=intent.new_content,
        destination_path=intent.destination_path,
        rollback_strategy=str(value["rollback_strategy"]),
    )


def _semantic(value: object) -> SemanticDiffEntry:
    assert isinstance(value, Mapping)
    destination = value["destination_path"]
    before = value["before_sha256"]
    after = value["after_sha256"]
    return SemanticDiffEntry(
        operation_id=OpaqueId(str(value["operation_id"])),
        kind=MutationKind(str(value["kind"])),
        path=RelativeArtifactPath(str(value["path"])),
        destination_path=(
            RelativeArtifactPath(str(destination)) if destination is not None else None
        ),
        before_sha256=HashDigest(str(before)) if before is not None else None,
        after_sha256=HashDigest(str(after)) if after is not None else None,
    )


def change_request_from_mapping(document: Mapping[str, object]) -> ChangeRequest:
    value = _schema_validated(document, "change-request/1.0")
    operations = value["operations"]
    assert isinstance(operations, list)
    request = ChangeRequest(
        request_id=OpaqueId(str(value["request_id"])),
        workspace_id=OpaqueId(str(value["workspace_id"])),
        requester_id=OpaqueId(str(value["requester_id"])),
        requested_at=str(value["requested_at"]),
        before_revision_id=OpaqueId(str(value["before_revision_id"])),
        before_manifest_sha256=HashDigest(str(value["before_manifest_sha256"])),
        idempotency_key=IdempotencyKey(str(value["idempotency_key"])),
        risk_tier=MutationRiskTier(str(value["risk_tier"])),
        operations=tuple(_intent(item) for item in operations),
    )
    try:
        change_request_to_mapping(request)
    except (MutationPlanError, MutationPathError) as error:
        raise MutationSerializationError(
            getattr(error, "reason_code", "mutation.serialization.contract"),
            str(error),
        ) from error
    return request


def mutation_plan_from_mapping(document: Mapping[str, object]) -> MutationPlan:
    value = _schema_validated(document, "mutation-plan/1.0")
    operations = value["operations"]
    semantic_diff = value["semantic_diff"]
    assert isinstance(operations, list) and isinstance(semantic_diff, list)
    plan = MutationPlan(
        plan_id=OpaqueId(str(value["plan_id"])),
        plan_sha256=HashDigest(str(value["plan_sha256"])),
        request_id=OpaqueId(str(value["request_id"])),
        change_request_sha256=HashDigest(str(value["change_request_sha256"])),
        workspace_id=OpaqueId(str(value["workspace_id"])),
        before_revision_id=OpaqueId(str(value["before_revision_id"])),
        before_workspace_revision_sha256=HashDigest(
            str(value["before_workspace_revision_sha256"])
        ),
        before_manifest_sha256=HashDigest(str(value["before_manifest_sha256"])),
        policy_bundle_sha256=HashDigest(str(value["policy_bundle_sha256"])),
        risk_policy_version=str(value["risk_policy_version"]),
        idempotency_key=IdempotencyKey(str(value["idempotency_key"])),
        risk_tier=MutationRiskTier(str(value["risk_tier"])),
        operations=tuple(_planned(item) for item in operations),
        semantic_diff=tuple(_semantic(item) for item in semantic_diff),
        requester_id=(
            OpaqueId(str(value["requester_id"]))
            if value.get("requester_id") is not None
            else None
        ),
    )
    try:
        validate_mutation_plan(plan)
    except MutationPlanError as error:
        raise MutationSerializationError(error.reason_code, str(error)) from error
    return plan


def mutation_receipt_from_mapping(document: Mapping[str, object]) -> MutationReceipt:
    value = _schema_validated(document, "mutation-receipt/1.0")
    raw_results = value["operation_results"]
    assert isinstance(raw_results, list)
    results: list[OperationResult] = []
    for item in raw_results:
        assert isinstance(item, Mapping)
        destination = item["destination_path"]
        before = item["before_sha256"]
        after = item["after_sha256"]
        results.append(
            OperationResult(
                operation_id=OpaqueId(str(item["operation_id"])),
                kind=MutationKind(str(item["kind"])),
                path=RelativeArtifactPath(str(item["path"])),
                destination_path=(
                    RelativeArtifactPath(str(destination))
                    if destination is not None
                    else None
                ),
                outcome=OperationOutcome(str(item["outcome"])),
                before_sha256=(HashDigest(str(before)) if before is not None else None),
                after_sha256=(HashDigest(str(after)) if after is not None else None),
                after_byte_length=(
                    int(item["after_byte_length"])
                    if item["after_byte_length"] is not None
                    else None
                ),
                reason_code=str(item["reason_code"]),
            )
        )
    receipt = MutationReceipt(
        receipt_id=OpaqueId(str(value["receipt_id"])),
        plan_id=OpaqueId(str(value["plan_id"])),
        plan_sha256=HashDigest(str(value["plan_sha256"])),
        workspace_id=OpaqueId(str(value["workspace_id"])),
        idempotency_key=IdempotencyKey(str(value["idempotency_key"])),
        executor_identity=OpaqueId(str(value["executor_identity"])),
        execution_authorization_id=OpaqueId(
            str(value["execution_authorization_id"])
        ),
        execution_authorization_sha256=HashDigest(
            str(value["execution_authorization_sha256"])
        ),
        before_workspace_observation_sha256=HashDigest(
            str(value["before_workspace_observation_sha256"])
        ),
        after_workspace_observation_sha256=HashDigest(
            str(value["after_workspace_observation_sha256"])
        ),
        idempotency_reservation=_reference(value["idempotency_reservation"]),
        journal_record=_reference(value["journal_record"]),
        started_at=str(value["started_at"]),
        completed_at=str(value["completed_at"]),
        status=MutationReceiptStatus(str(value["status"])),
        before_manifest_sha256=HashDigest(str(value["before_manifest_sha256"])),
        after_manifest_sha256=HashDigest(str(value["after_manifest_sha256"])),
        operation_results=tuple(results),
        rollback_or_reconciliation_required=bool(
            value["rollback_or_reconciliation_required"]
        ),
        rollback_or_reconciliation_plan=(
            _reference(value["rollback_or_reconciliation_plan"])
            if value["rollback_or_reconciliation_plan"] is not None
            else None
        ),
    )
    try:
        return validate_mutation_receipt(receipt)
    except MutationPlanError as error:
        raise MutationSerializationError(error.reason_code, str(error)) from error


def workspace_revision_from_mapping(document: Mapping[str, object]) -> WorkspaceRevision:
    value = _schema_validated(document, "workspace-revision/1.0")
    raw_entries = value["entries"]
    assert isinstance(raw_entries, list)
    entries: list[RevisionEntry] = []
    for item in raw_entries:
        assert isinstance(item, Mapping)
        digest = item["content_sha256"]
        entries.append(
            RevisionEntry(
                path=RelativeArtifactPath(str(item["path"])),
                revision_ordinal=int(item["revision_ordinal"]),
                content_sha256=(HashDigest(str(digest)) if digest is not None else None),
                byte_length=(
                    int(item["byte_length"])
                    if item["byte_length"] is not None
                    else None
                ),
                tombstone=bool(item["tombstone"]),
                source_operation_id=OpaqueId(str(item["source_operation_id"])),
            )
        )
    parent = value["parent_revision_id"]
    reconciliation = value["reconciliation_evidence"]
    revision = WorkspaceRevision(
        revision_id=OpaqueId(str(value["revision_id"])),
        workspace_id=OpaqueId(str(value["workspace_id"])),
        origin=WorkspaceRevisionOrigin(str(value["origin"])),
        parent_revision_id=OpaqueId(str(parent)) if parent is not None else None,
        reconciliation_evidence=(
            _reference(reconciliation) if reconciliation is not None else None
        ),
        manifest_sha256=HashDigest(str(value["manifest_sha256"])),
        created_at=str(value["created_at"]),
        plan_id=OpaqueId(str(value["plan_id"])),
        receipt_id=OpaqueId(str(value["receipt_id"])),
        trust_state=WorkspaceTrustState(str(value["trust_state"])),
        entries=tuple(entries),
    )
    try:
        return validate_workspace_revision(revision)
    except (MutationPlanError, MutationPathError) as error:
        raise MutationSerializationError(
            getattr(error, "reason_code", "mutation.serialization.contract"),
            str(error),
        ) from error


def drift_report_from_mapping(document: Mapping[str, object]) -> DriftReport:
    value = _schema_validated(document, "drift-report/1.0")
    raw_findings = value["findings"]
    assert isinstance(raw_findings, list)
    findings: list[DriftFinding] = []
    for item in raw_findings:
        assert isinstance(item, Mapping)
        expected = item["expected_sha256"]
        observed = item["observed_sha256"]
        findings.append(
            DriftFinding(
                path=RelativeArtifactPath(str(item["path"])),
                kind=DriftKind(str(item["kind"])),
                expected_sha256=(
                    HashDigest(str(expected)) if expected is not None else None
                ),
                observed_sha256=(
                    HashDigest(str(observed)) if observed is not None else None
                ),
                reason_code=str(item["reason_code"]),
            )
        )
    report = DriftReport(
        report_id=OpaqueId(str(value["report_id"])),
        workspace_id=OpaqueId(str(value["workspace_id"])),
        expected_revision_id=OpaqueId(str(value["expected_revision_id"])),
        expected_manifest_sha256=HashDigest(
            str(value["expected_manifest_sha256"])
        ),
        observed_manifest_sha256=HashDigest(
            str(value["observed_manifest_sha256"])
        ),
        detected_at=str(value["detected_at"]),
        trust_state=WorkspaceTrustState(str(value["trust_state"])),
        findings=tuple(findings),
        generation_blocked=bool(value["generation_blocked"]),
        publish_blocked=bool(value["publish_blocked"]),
        dependent_plans_invalidated=bool(value["dependent_plans_invalidated"]),
        authority_invalidated=bool(value["authority_invalidated"]),
        qc_invalidated=bool(value["qc_invalidated"]),
        reconciliation_required=bool(value["reconciliation_required"]),
    )
    try:
        return validate_drift_report(report)
    except (MutationPlanError, MutationPathError) as error:
        raise MutationSerializationError(
            getattr(error, "reason_code", "mutation.serialization.contract"),
            str(error),
        ) from error


def break_glass_authorization_from_mapping(
    document: Mapping[str, object],
) -> BreakGlassAuthorization:
    value = _schema_validated(document, "break-glass-authorization/1.0")
    raw_approvals = value["approvals"]
    raw_paths = value["exact_paths"]
    raw_kinds = value["operation_kinds"]
    assert isinstance(raw_approvals, list)
    assert isinstance(raw_paths, list)
    assert isinstance(raw_kinds, list)
    try:
        exact_paths = require_collision_free(
            (str(item) for item in raw_paths), label="break-glass exact paths"
        )
    except MutationPathError as error:
        raise MutationSerializationError(error.reason_code, str(error)) from error
    approvals = tuple(
        AuthenticatedHumanApproval(
            principal_id=OpaqueId(str(item["principal_id"])),
            break_glass_request_sha256=HashDigest(
                str(item["break_glass_request_sha256"])
            ),
            authentication_evidence=_reference(item["authentication_evidence"]),
            approval_record=_reference(item["approval_record"]),
            approved_at=str(item["approved_at"]),
        )
        for item in raw_approvals
        if isinstance(item, Mapping)
    )
    return BreakGlassAuthorization(
        authorization_id=OpaqueId(str(value["authorization_id"])),
        plan_sha256=HashDigest(str(value["plan_sha256"])),
        workspace_id=OpaqueId(str(value["workspace_id"])),
        before_revision_id=OpaqueId(str(value["before_revision_id"])),
        before_manifest_sha256=HashDigest(str(value["before_manifest_sha256"])),
        issued_at=str(value["issued_at"]),
        expires_at=str(value["expires_at"]),
        exact_paths=exact_paths,
        operation_kinds=frozenset(MutationKind(str(item)) for item in raw_kinds),
        approvals=approvals,
        pre_change_snapshot=_reference(value["pre_change_snapshot"]),
        incident_id=OpaqueId(str(value["incident_id"])),
        incident_record=_reference(value["incident_record"]),
        audit_record=_reference(value["audit_record"]),
        session_id=OpaqueId(str(value["session_id"])),
        executor_identity=OpaqueId(str(value["executor_identity"])),
        standing_grant=bool(value["standing_grant"]),
        risk_tier=MutationRiskTier(str(value["risk_tier"])),
        reconciliation_required=bool(value["reconciliation_required"]),
        post_change_validation_required=bool(
            value["post_change_validation_required"]
        ),
    )


_FROM_MAPPING = {
    "change-request/1.0": change_request_from_mapping,
    "mutation-plan/1.0": mutation_plan_from_mapping,
    "mutation-receipt/1.0": mutation_receipt_from_mapping,
    "workspace-revision/1.0": workspace_revision_from_mapping,
    "drift-report/1.0": drift_report_from_mapping,
    "break-glass-authorization/1.0": break_glass_authorization_from_mapping,
}


def mutation_artifact_from_mapping(document: Mapping[str, object]) -> MutationArtifact:
    try:
        normalized = validate_json_mapping(document)
    except JsonInputError as error:
        raise MutationSerializationError(error.code.value, str(error)) from error
    version = normalized.get("artifact_version")
    parser = _FROM_MAPPING.get(version) if isinstance(version, str) else None
    if parser is None:
        raise MutationSerializationError(
            "mutation.serialization.version",
            f"unsupported mutation artifact_version: {version!r}",
        )
    return parser(normalized)


def mutation_artifact_from_bytes(payload: bytes) -> MutationArtifact:
    try:
        document = require_json_object(parse_json_bytes(payload))
    except JsonInputError as error:
        raise MutationSerializationError(error.code.value, str(error)) from error
    return mutation_artifact_from_mapping(document)


def mutation_artifact_to_mapping(artifact: MutationArtifact) -> dict[str, object]:
    if isinstance(artifact, ChangeRequest):
        return change_request_to_mapping(artifact)
    if isinstance(artifact, MutationPlan):
        return mutation_plan_to_mapping(artifact)
    if isinstance(artifact, MutationReceipt):
        return mutation_receipt_to_mapping(artifact)
    if isinstance(artifact, WorkspaceRevision):
        return workspace_revision_to_mapping(artifact)
    if isinstance(artifact, DriftReport):
        return drift_report_to_mapping(artifact)
    if isinstance(artifact, BreakGlassAuthorization):
        return break_glass_authorization_to_mapping(artifact)
    raise MutationSerializationError(
        "mutation.serialization.type",
        "unsupported mutation artifact type",
    )


def mutation_artifact_sha256(artifact: MutationArtifact) -> HashDigest:
    return canonical_sha256(mutation_artifact_to_mapping(artifact))
