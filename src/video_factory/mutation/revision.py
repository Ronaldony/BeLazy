"""Pure immutable revision derivation and out-of-band drift detection."""

from __future__ import annotations

from video_factory.config.canonical import canonical_sha256
from video_factory.domain import HashDigest, OpaqueId, RelativeArtifactPath
from video_factory.json_boundary import parse_rfc3339_datetime

from .contracts import (
    DriftFinding,
    DriftKind,
    DriftReport,
    MutationKind,
    MutationExecutionAuthorization,
    MutationPlan,
    MutationReceipt,
    MutationReceiptStatus,
    OperationOutcome,
    PathNodeKind,
    RevisionEntry,
    WorkspaceObservation,
    WorkspaceRevision,
    WorkspaceRevisionOrigin,
    WorkspaceTrustState,
)
from .paths import (
    MutationPathError,
    observation_index,
    require_managed_path,
    require_no_link_or_reparse_ancestor,
)
from .planner import (
    MutationPlanError,
    validate_mutation_plan,
    validate_mutation_execution_authorization,
    validate_mutation_receipt,
    validate_workspace_revision,
    mutation_execution_authorization_sha256,
    workspace_revision_to_mapping,
)
from .trust import (
    WorkspaceTrustError,
    workspace_observation_mapping,
    workspace_observation_sha256,
)


class WorkspaceRevisionError(ValueError):
    """Raised when a receipt cannot produce one trusted immutable revision."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _latest_entries(revision: WorkspaceRevision) -> dict[str, RevisionEntry]:
    latest: dict[str, RevisionEntry] = {}
    for entry in revision.entries:
        path = str(require_managed_path(entry.path))
        if entry.revision_ordinal < 1:
            raise WorkspaceRevisionError(
                "mutation.revision.ordinal",
                "revision ordinals must be positive",
            )
        existing = latest.get(path)
        if existing is None or entry.revision_ordinal > existing.revision_ordinal:
            latest[path] = entry
        elif entry.revision_ordinal == existing.revision_ordinal:
            raise WorkspaceRevisionError(
                "mutation.revision.duplicate_ordinal",
                f"duplicate revision ordinal for {path!r}",
            )
    return latest


def derive_workspace_revision(
    previous: WorkspaceRevision,
    plan: MutationPlan,
    receipt: MutationReceipt,
    authorization: MutationExecutionAuthorization,
    after_observation: WorkspaceObservation,
) -> WorkspaceRevision:
    """Promote only an exactly authorized and byte-verified successful receipt."""

    try:
        validate_mutation_plan(plan)
        validate_mutation_receipt(receipt)
        validate_workspace_revision(previous)
        validate_mutation_execution_authorization(authorization)
    except MutationPlanError as error:
        raise WorkspaceRevisionError(error.reason_code, str(error)) from error
    try:
        workspace_observation_mapping(after_observation)
    except WorkspaceTrustError as error:
        raise WorkspaceRevisionError(error.reason_code, str(error)) from error
    if (
        canonical_sha256(workspace_revision_to_mapping(previous))
        != plan.before_workspace_revision_sha256
    ):
        raise WorkspaceRevisionError(
            "mutation.revision.parent_digest_mismatch",
            "plan is bound to another complete parent revision",
        )
    if previous.trust_state is not WorkspaceTrustState.TRUSTED:
        raise WorkspaceRevisionError(
            "mutation.revision.untrusted_parent",
            "an untrusted workspace cannot advance without reconciliation",
        )
    if previous.workspace_id != plan.workspace_id or receipt.workspace_id != plan.workspace_id:
        raise WorkspaceRevisionError(
            "mutation.revision.workspace_mismatch",
            "plan, receipt, and previous revision must share a workspace",
        )
    if previous.revision_id != plan.before_revision_id:
        raise WorkspaceRevisionError(
            "mutation.revision.stale_parent",
            "plan is bound to another workspace revision",
        )
    if previous.manifest_sha256 != plan.before_manifest_sha256:
        raise WorkspaceRevisionError(
            "mutation.revision.stale_manifest",
            "plan is bound to another workspace manifest",
        )
    if (
        receipt.plan_id != plan.plan_id
        or receipt.plan_sha256 != plan.plan_sha256
        or receipt.idempotency_key != plan.idempotency_key
        or receipt.before_manifest_sha256 != plan.before_manifest_sha256
    ):
        raise WorkspaceRevisionError(
            "mutation.revision.receipt_binding",
            "receipt is not bound to the exact plan and before manifest",
        )
    if (
        authorization.plan_id != plan.plan_id
        or authorization.plan_sha256 != plan.plan_sha256
        or authorization.workspace_id != plan.workspace_id
        or authorization.revision_id != plan.before_revision_id
        or authorization.workspace_revision_sha256
        != plan.before_workspace_revision_sha256
        or authorization.manifest_sha256 != plan.before_manifest_sha256
        or authorization.idempotency_key != plan.idempotency_key
    ):
        raise WorkspaceRevisionError(
            "mutation.revision.authorization_binding",
            "execution authorization is not bound to the exact plan and parent state",
        )
    if (
        receipt.execution_authorization_id != authorization.authorization_id
        or receipt.execution_authorization_sha256
        != mutation_execution_authorization_sha256(authorization)
        or receipt.before_workspace_observation_sha256
        != authorization.workspace_observation_sha256
        or receipt.idempotency_reservation
        != authorization.idempotency_reservation
        or receipt.executor_identity != authorization.service_identity
    ):
        raise WorkspaceRevisionError(
            "mutation.revision.receipt_authorization_binding",
            "receipt does not preserve the exact execution authorization evidence",
        )
    if (
        workspace_observation_sha256(after_observation)
        != receipt.after_workspace_observation_sha256
    ):
        raise WorkspaceRevisionError(
            "mutation.revision.after_observation_digest",
            "receipt is bound to another after-workspace observation",
        )
    if (
        after_observation.workspace_id != plan.workspace_id
        or after_observation.revision_id != plan.before_revision_id
        or after_observation.manifest_sha256 != receipt.after_manifest_sha256
        or after_observation.trust_state is not WorkspaceTrustState.TRUSTED
        or after_observation.complete is not True
    ):
        raise WorkspaceRevisionError(
            "mutation.revision.after_observation_binding",
            "after-workspace observation is incomplete, untrusted, or bound elsewhere",
        )
    if receipt.status is not MutationReceiptStatus.SUCCEEDED:
        raise WorkspaceRevisionError(
            "mutation.revision.incomplete_receipt",
            "only a fully successful receipt may create a trusted revision",
        )
    if receipt.rollback_or_reconciliation_required:
        raise WorkspaceRevisionError(
            "mutation.revision.reconciliation_required",
            "receipt requires reconciliation before a trusted revision",
        )
    if receipt.after_manifest_sha256 is None:
        raise WorkspaceRevisionError(
            "mutation.revision.after_manifest_missing",
            "successful receipt must bind an after-manifest digest",
        )
    try:
        started = parse_rfc3339_datetime(receipt.started_at)
        completed = parse_rfc3339_datetime(receipt.completed_at)
        authorized_at = parse_rfc3339_datetime(authorization.evaluated_at)
    except ValueError as error:
        raise WorkspaceRevisionError(
            "mutation.revision.datetime",
            "receipt times must be RFC 3339 date-times",
        ) from error
    if completed < started:
        raise WorkspaceRevisionError(
            "mutation.revision.time_order",
            "receipt completion precedes its start",
        )
    if authorized_at != started:
        raise WorkspaceRevisionError(
            "mutation.revision.authorization_time",
            "receipt must start at the immediate authorization evaluation time",
        )

    result_by_id = {str(item.operation_id): item for item in receipt.operation_results}
    if len(result_by_id) != len(receipt.operation_results):
        raise WorkspaceRevisionError(
            "mutation.revision.duplicate_result",
            "receipt contains duplicate operation results",
        )
    if set(result_by_id) != {str(item.operation_id) for item in plan.operations}:
        raise WorkspaceRevisionError(
            "mutation.revision.result_set",
            "receipt must cover every planned operation exactly once",
        )
    latest = _latest_entries(previous)
    appended: list[RevisionEntry] = []

    def append_entry(
        path: RelativeArtifactPath,
        *,
        content_sha256: HashDigest | None,
        byte_length: int | None,
        tombstone: bool,
        operation_id: OpaqueId,
    ) -> None:
        existing = latest.get(str(path))
        ordinal = 1 if existing is None else existing.revision_ordinal + 1
        entry = RevisionEntry(
            path=path,
            revision_ordinal=ordinal,
            content_sha256=content_sha256,
            byte_length=byte_length,
            tombstone=tombstone,
            source_operation_id=operation_id,
        )
        appended.append(entry)
        latest[str(path)] = entry

    for operation in plan.operations:
        result = result_by_id[str(operation.operation_id)]
        if (
            result.outcome is not OperationOutcome.APPLIED
            or result.kind is not operation.kind
            or result.path != operation.path
            or result.destination_path != operation.destination_path
        ):
            raise WorkspaceRevisionError(
                "mutation.revision.operation_result",
                "successful receipt contains an invalid operation result",
            )
        if result.before_sha256 != operation.expected_before.exact_sha256:
            raise WorkspaceRevisionError(
                "mutation.revision.before_digest",
                "operation result before digest does not match the plan",
            )
        if operation.kind in {MutationKind.CREATE, MutationKind.REPLACE}:
            assert operation.new_content is not None
            if (
                result.after_sha256 != operation.new_content.exact_sha256
                or result.after_byte_length != operation.new_content.byte_length
            ):
                raise WorkspaceRevisionError(
                    "mutation.revision.after_content",
                    "operation result does not match planned new content",
                )
            append_entry(
                operation.path,
                content_sha256=result.after_sha256,
                byte_length=result.after_byte_length,
                tombstone=False,
                operation_id=operation.operation_id,
            )
        elif operation.kind is MutationKind.DELETE:
            if result.after_sha256 is not None or result.after_byte_length is not None:
                raise WorkspaceRevisionError(
                    "mutation.revision.delete_after_content",
                    "delete result must not carry after content",
                )
            append_entry(
                operation.path,
                content_sha256=None,
                byte_length=None,
                tombstone=True,
                operation_id=operation.operation_id,
            )
        else:
            assert operation.destination_path is not None
            if result.after_sha256 != operation.expected_before.exact_sha256:
                raise WorkspaceRevisionError(
                    "mutation.revision.move_digest",
                    "move must preserve the exact source digest",
                )
            if result.after_byte_length is None or result.after_byte_length < 0:
                raise WorkspaceRevisionError(
                    "mutation.revision.move_length",
                    "move must record the destination byte length",
                )
            append_entry(
                operation.path,
                content_sha256=None,
                byte_length=None,
                tombstone=True,
                operation_id=operation.operation_id,
            )
            append_entry(
                operation.destination_path,
                content_sha256=result.after_sha256,
                byte_length=result.after_byte_length,
                tombstone=False,
                operation_id=operation.operation_id,
            )

    observed_files = {
        str(item.path): item
        for item in after_observation.entries
        if item.node_kind is PathNodeKind.FILE
    }
    expected_active = {
        path: entry for path, entry in latest.items() if not entry.tombstone
    }
    if set(observed_files) != set(expected_active):
        raise WorkspaceRevisionError(
            "mutation.revision.after_content_set",
            "after-workspace files do not match the derived active revision",
        )
    for path, entry in expected_active.items():
        observed = observed_files[path]
        if (
            observed.exact_sha256 != entry.content_sha256
            or observed.byte_length != entry.byte_length
        ):
            raise WorkspaceRevisionError(
                "mutation.revision.after_content_mismatch",
                f"after-workspace bytes do not match the derived entry: {path}",
            )
    if any(
        item.node_kind in {PathNodeKind.SYMLINK, PathNodeKind.REPARSE}
        for item in after_observation.entries
    ):
        raise WorkspaceRevisionError(
            "mutation.revision.after_link_or_reparse",
            "after-workspace observation contains a link or reparse point",
        )
    try:
        after_index = observation_index(after_observation.entries)
        for path in expected_active:
            require_no_link_or_reparse_ancestor(path, after_index)
    except MutationPathError as error:
        raise WorkspaceRevisionError(error.reason_code, str(error)) from error

    identity = {
        "workspace_id": str(plan.workspace_id),
        "parent_revision_id": str(previous.revision_id),
        "plan_id": str(plan.plan_id),
        "receipt_id": str(receipt.receipt_id),
        "manifest_sha256": str(receipt.after_manifest_sha256),
    }
    revision_id = OpaqueId(f"revision-{str(canonical_sha256(identity))[:24]}")
    revision = WorkspaceRevision(
        revision_id=revision_id,
        workspace_id=plan.workspace_id,
        origin=WorkspaceRevisionOrigin.MANAGED_MUTATION,
        parent_revision_id=previous.revision_id,
        reconciliation_evidence=None,
        manifest_sha256=receipt.after_manifest_sha256,
        created_at=receipt.completed_at,
        plan_id=plan.plan_id,
        receipt_id=receipt.receipt_id,
        trust_state=WorkspaceTrustState.TRUSTED,
        entries=tuple(previous.entries) + tuple(appended),
    )
    try:
        return validate_workspace_revision(revision)
    except MutationPlanError as error:
        raise WorkspaceRevisionError(error.reason_code, str(error)) from error


def detect_workspace_drift(
    expected: WorkspaceRevision,
    observed: WorkspaceObservation,
    *,
    detected_at: str,
) -> DriftReport | None:
    """Compare immutable revision evidence to an observed manifest and files."""

    try:
        parse_rfc3339_datetime(detected_at)
    except ValueError as error:
        raise WorkspaceRevisionError(
            "mutation.drift.datetime",
            "detected_at must be an RFC 3339 date-time",
        ) from error
    if expected.workspace_id != observed.workspace_id:
        raise WorkspaceRevisionError(
            "mutation.drift.workspace_mismatch",
            "expected and observed workspaces differ",
        )
    latest = _latest_entries(expected)
    active = {path: entry for path, entry in latest.items() if not entry.tombstone}
    findings: list[DriftFinding] = []
    try:
        observed_by_path = dict(observation_index(observed.entries))
    except MutationPathError:
        findings.append(
            DriftFinding(
                path=RelativeArtifactPath("workspace-path-alias"),
                kind=DriftKind.PATH_ALIAS,
                expected_sha256=None,
                observed_sha256=None,
                reason_code="mutation.drift.path_alias",
            )
        )
        observed_by_path = {}

    if observed.complete is not True:
        findings.append(
            DriftFinding(
                path=RelativeArtifactPath("workspace-observation"),
                kind=DriftKind.MANIFEST_MISMATCH,
                expected_sha256=None,
                observed_sha256=None,
                reason_code="mutation.drift.observation_incomplete",
            )
        )

    for path, entry in sorted(active.items()):
        item = observed_by_path.pop(path, None)
        if item is None:
            findings.append(
                DriftFinding(
                    RelativeArtifactPath(path),
                    DriftKind.MISSING,
                    entry.content_sha256,
                    None,
                    "mutation.drift.missing",
                )
            )
        elif item.node_kind in {PathNodeKind.SYMLINK, PathNodeKind.REPARSE}:
            findings.append(
                DriftFinding(
                    item.path,
                    DriftKind.LINK_OR_REPARSE,
                    entry.content_sha256,
                    item.exact_sha256,
                    "mutation.drift.link_or_reparse",
                )
            )
        elif item.node_kind is not PathNodeKind.FILE:
            findings.append(
                DriftFinding(
                    item.path,
                    DriftKind.TYPE_CHANGED,
                    entry.content_sha256,
                    item.exact_sha256,
                    "mutation.drift.type_changed",
                )
            )
        elif item.exact_sha256 != entry.content_sha256:
            findings.append(
                DriftFinding(
                    item.path,
                    DriftKind.MODIFIED,
                    entry.content_sha256,
                    item.exact_sha256,
                    "mutation.drift.modified",
                )
            )
    for path, item in sorted(observed_by_path.items()):
        # Directory observations are structural path-safety evidence, not
        # managed content entries.  Keep links/reparse points visible while
        # avoiding false drift for ordinary parent directories.
        if item.node_kind is PathNodeKind.DIRECTORY:
            continue
        findings.append(
            DriftFinding(
                RelativeArtifactPath(path),
                (
                    DriftKind.LINK_OR_REPARSE
                    if item.node_kind in {PathNodeKind.SYMLINK, PathNodeKind.REPARSE}
                    else DriftKind.ADDED
                ),
                None,
                item.exact_sha256,
                (
                    "mutation.drift.link_or_reparse"
                    if item.node_kind in {PathNodeKind.SYMLINK, PathNodeKind.REPARSE}
                    else "mutation.drift.added"
                ),
            )
        )
    if observed.manifest_sha256 != expected.manifest_sha256:
        findings.append(
            DriftFinding(
                RelativeArtifactPath("workspace-manifest"),
                DriftKind.MANIFEST_MISMATCH,
                expected.manifest_sha256,
                observed.manifest_sha256,
                "mutation.drift.manifest_mismatch",
            )
        )
    if observed.revision_id != expected.revision_id:
        findings.append(
            DriftFinding(
                RelativeArtifactPath("workspace-revision"),
                DriftKind.MANIFEST_MISMATCH,
                None,
                None,
                "mutation.drift.revision_mismatch",
            )
        )
    if not findings and observed.trust_state is WorkspaceTrustState.TRUSTED:
        return None
    if not findings:
        findings.append(
            DriftFinding(
                RelativeArtifactPath("workspace-trust"),
                DriftKind.MANIFEST_MISMATCH,
                None,
                None,
                "mutation.drift.preexisting_untrusted_state",
            )
        )
    ordered = tuple(
        sorted(findings, key=lambda item: (str(item.path), item.kind.value, item.reason_code))
    )
    identity = {
        "workspace_id": str(expected.workspace_id),
        "expected_revision_id": str(expected.revision_id),
        "expected_manifest_sha256": str(expected.manifest_sha256),
        "observed_manifest_sha256": str(observed.manifest_sha256),
        "detected_at": detected_at,
        "findings": [
            {
                "path": str(item.path),
                "kind": item.kind.value,
                "expected_sha256": (
                    str(item.expected_sha256) if item.expected_sha256 is not None else None
                ),
                "observed_sha256": (
                    str(item.observed_sha256) if item.observed_sha256 is not None else None
                ),
                "reason_code": item.reason_code,
            }
            for item in ordered
        ],
    }
    return DriftReport(
        report_id=OpaqueId(f"drift-{str(canonical_sha256(identity))[:24]}"),
        workspace_id=expected.workspace_id,
        expected_revision_id=expected.revision_id,
        expected_manifest_sha256=expected.manifest_sha256,
        observed_manifest_sha256=observed.manifest_sha256,
        detected_at=detected_at,
        trust_state=WorkspaceTrustState.UNTRUSTED,
        findings=ordered,
        generation_blocked=True,
        publish_blocked=True,
        dependent_plans_invalidated=True,
        authority_invalidated=True,
        qc_invalidated=True,
        reconciliation_required=True,
    )
