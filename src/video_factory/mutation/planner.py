"""Deterministic pure planner and document mappings for managed mutation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
import re

from video_factory.config.canonical import canonical_sha256
from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    HashDigest,
    IdempotencyKey,
    OpaqueId,
    RelativeArtifactPath,
)
from video_factory.json_boundary import parse_rfc3339_datetime

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
    MutationExecutionAuthorization,
    MutationOperationIntent,
    MutationPlan,
    MutationReceipt,
    MutationReceiptStatus,
    MutationRiskTier,
    OperationOutcome,
    OperationResult,
    PathNodeKind,
    PlannedMutationOperation,
    RevisionEntry,
    SemanticDiffEntry,
    WorkspaceObservation,
    WorkspaceRevision,
    WorkspaceRevisionOrigin,
    WorkspaceTrustState,
)
from .paths import (
    MutationPathError,
    observation_index,
    path_collision_key,
    require_collision_free,
    require_managed_path,
    require_no_link_or_reparse_ancestor,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OPAQUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ROLLBACK = "restore_previous_revision"
MANAGED_MUTATION_RISK_POLICY_VERSION = "managed-mutation-risk-policy/1.0"
_RISK_RANK = {
    MutationRiskTier.R1: 1,
    MutationRiskTier.R2: 2,
    MutationRiskTier.R3: 3,
    MutationRiskTier.R4: 4,
}


class MutationPlanError(ValueError):
    """Stable planning or mapping failure."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _opaque(value: object, label: str) -> OpaqueId:
    if not isinstance(value, str) or _OPAQUE.fullmatch(value) is None:
        raise MutationPlanError(
            "mutation.contract.opaque_id",
            f"{label} must be a valid opaque identifier",
        )
    return OpaqueId(value)


def _sha256(value: object, label: str) -> HashDigest:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise MutationPlanError(
            "mutation.contract.sha256",
            f"{label} must be a lowercase sha256 digest",
        )
    return HashDigest(value)


def _timestamp(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise MutationPlanError(
            "mutation.contract.datetime",
            f"{label} must be an RFC 3339 date-time",
        )
    try:
        parse_rfc3339_datetime(value)
    except ValueError as error:
        raise MutationPlanError(
            "mutation.contract.datetime",
            f"{label} must be an RFC 3339 date-time",
        ) from error
    return value


def _content(value: ContentObject, label: str) -> ContentObject:
    if not isinstance(value, ContentObject):
        raise MutationPlanError(
            "mutation.operation.content_invalid",
            f"{label} must be a ContentObject",
        )
    if not isinstance(value.byte_length, int) or isinstance(value.byte_length, bool) or value.byte_length < 0:
        raise MutationPlanError(
            "mutation.operation.byte_length",
            f"{label}.byte_length must be a non-negative integer",
        )
    return ContentObject(
        object_id=_opaque(str(value.object_id), f"{label}.object_id"),
        exact_sha256=_sha256(str(value.exact_sha256), f"{label}.exact_sha256"),
        byte_length=value.byte_length,
    )


def _expected(value: ExpectedBefore, label: str) -> ExpectedBefore:
    if not isinstance(value, ExpectedBefore) or not isinstance(value.exists, bool):
        raise MutationPlanError(
            "mutation.operation.expected_before",
            f"{label} must be an ExpectedBefore",
        )
    if value.exists:
        if value.exact_sha256 is None:
            raise MutationPlanError(
                "mutation.operation.exact_before_required",
                f"{label}.exact_sha256 is required when exists is true",
            )
        digest = _sha256(str(value.exact_sha256), f"{label}.exact_sha256")
    else:
        if value.exact_sha256 is not None:
            raise MutationPlanError(
                "mutation.operation.unexpected_before_digest",
                f"{label}.exact_sha256 must be null when exists is false",
            )
        digest = None
    return ExpectedBefore(value.exists, digest)


def _validate_intent(
    operation: MutationOperationIntent,
    *,
    index: int,
) -> MutationOperationIntent:
    if not isinstance(operation, MutationOperationIntent):
        raise MutationPlanError(
            "mutation.operation.type",
            f"operations[{index}] must be a MutationOperationIntent",
        )
    try:
        kind = MutationKind(operation.kind)
        path = require_managed_path(operation.path, label=f"operations[{index}].path")
        destination = (
            require_managed_path(
                operation.destination_path,
                label=f"operations[{index}].destination_path",
            )
            if operation.destination_path is not None
            else None
        )
    except (ValueError, MutationPathError) as error:
        if isinstance(error, MutationPathError):
            raise MutationPlanError(error.reason_code, str(error)) from error
        raise MutationPlanError(
            "mutation.operation.kind",
            f"operations[{index}].kind is unsupported",
        ) from error
    expected = _expected(operation.expected_before, f"operations[{index}].expected_before")
    content = (
        _content(operation.new_content, f"operations[{index}].new_content")
        if operation.new_content is not None
        else None
    )

    if kind is MutationKind.CREATE:
        valid = not expected.exists and content is not None and destination is None
        reason = "create requires missing target, new content, and no destination"
    elif kind is MutationKind.REPLACE:
        valid = expected.exists and content is not None and destination is None
        reason = "replace requires exact-before, new content, and no destination"
        if valid and content.exact_sha256 == expected.exact_sha256:
            raise MutationPlanError(
                "mutation.operation.noop_replace",
                "replace must change the exact content digest",
            )
    elif kind is MutationKind.DELETE:
        valid = expected.exists and content is None and destination is None
        reason = "delete requires exact-before and no content or destination"
    else:
        valid = expected.exists and content is None and destination is not None
        reason = "move requires exact-before, destination, and no new content"
        if valid and destination == path:
            raise MutationPlanError(
                "mutation.operation.noop_move",
                "move source and destination must differ",
            )
    if not valid:
        raise MutationPlanError("mutation.operation.shape", reason)
    return MutationOperationIntent(kind, path, expected, content, destination)


def _intent_mapping(operation: MutationOperationIntent) -> dict[str, object]:
    return {
        "kind": operation.kind.value,
        "path": str(operation.path),
        "destination_path": (
            str(operation.destination_path)
            if operation.destination_path is not None
            else None
        ),
        "expected_before": {
            "exists": operation.expected_before.exists,
            "exact_sha256": (
                str(operation.expected_before.exact_sha256)
                if operation.expected_before.exact_sha256 is not None
                else None
            ),
        },
        "new_content": (
            {
                "object_id": str(operation.new_content.object_id),
                "exact_sha256": str(operation.new_content.exact_sha256),
                "byte_length": operation.new_content.byte_length,
            }
            if operation.new_content is not None
            else None
        ),
    }


def _planned_mapping(operation: PlannedMutationOperation) -> dict[str, object]:
    mapping = _intent_mapping(
        MutationOperationIntent(
            operation.kind,
            operation.path,
            operation.expected_before,
            operation.new_content,
            operation.destination_path,
        )
    )
    return {
        "operation_id": str(operation.operation_id),
        **mapping,
        "rollback_strategy": operation.rollback_strategy,
    }


def _semantic_diff_mapping(entry: SemanticDiffEntry) -> dict[str, object]:
    return {
        "operation_id": str(entry.operation_id),
        "kind": entry.kind.value,
        "path": str(entry.path),
        "destination_path": (
            str(entry.destination_path)
            if entry.destination_path is not None
            else None
        ),
        "before_sha256": (
            str(entry.before_sha256) if entry.before_sha256 is not None else None
        ),
        "after_sha256": (
            str(entry.after_sha256) if entry.after_sha256 is not None else None
        ),
    }


def _require_nonoverlapping_paths(
    operations: Sequence[MutationOperationIntent],
) -> None:
    touched = [
        path
        for operation in operations
        for path in (operation.path, operation.destination_path)
        if path is not None
    ]
    try:
        normalized = require_collision_free(touched, label="mutation operation paths")
    except MutationPathError as error:
        raise MutationPlanError(error.reason_code, str(error)) from error
    paths = sorted((path_collision_key(path), str(path)) for path in normalized)
    for left_index, (left_key, left) in enumerate(paths):
        for right_key, right in paths[left_index + 1 :]:
            if right_key.startswith(left_key + "/"):
                raise MutationPlanError(
                    "mutation.path.overlap",
                    f"mutation paths overlap: {left!r} and {right!r}",
                )


def _require_consistent_content_objects(
    operations: Sequence[MutationOperationIntent],
) -> None:
    """Reject one immutable object ID being assigned conflicting bytes."""

    observed: dict[str, tuple[HashDigest, int]] = {}
    for operation in operations:
        content = operation.new_content
        if content is None:
            continue
        key = str(content.object_id)
        identity = (content.exact_sha256, content.byte_length)
        previous = observed.get(key)
        if previous is not None and previous != identity:
            raise MutationPlanError(
                "mutation.content.object_identity_conflict",
                f"content object ID is bound to conflicting bytes: {key}",
            )
        observed[key] = identity


def _minimum_risk_tier(
    operations: Sequence[MutationOperationIntent],
) -> MutationRiskTier:
    """Apply the conservative W02 risk floor.

    W02 has no trusted semantic risk-classification evidence contract yet. A
    path name cannot prove that a CREATE is harmless, so every mutation is R4
    until W04 supplies a current policy-bound classifier decision.
    """

    if not operations:
        raise MutationPlanError(
            "mutation.request.operations_empty",
            "risk classification requires at least one operation",
        )
    return MutationRiskTier.R4


def _effective_risk_tier(
    declared: MutationRiskTier,
    operations: Sequence[MutationOperationIntent],
) -> MutationRiskTier:
    required = _minimum_risk_tier(operations)
    return declared if _RISK_RANK[declared] >= _RISK_RANK[required] else required


def _change_request_mapping(request: ChangeRequest) -> dict[str, object]:
    return {
        "artifact_version": "change-request/1.0",
        "request_id": str(request.request_id),
        "workspace_id": str(request.workspace_id),
        "requester_id": str(request.requester_id),
        "requested_at": request.requested_at,
        "before_revision_id": str(request.before_revision_id),
        "before_manifest_sha256": str(request.before_manifest_sha256),
        "idempotency_key": str(request.idempotency_key),
        "risk_tier": request.risk_tier.value,
        "operations": [_intent_mapping(operation) for operation in request.operations],
    }


def _plan_projection(
    *,
    request_id: OpaqueId,
    change_request_sha256: HashDigest,
    workspace_id: OpaqueId,
    before_revision_id: OpaqueId,
    before_workspace_revision_sha256: HashDigest,
    before_manifest_sha256: HashDigest,
    policy_bundle_sha256: HashDigest,
    risk_policy_version: str,
    idempotency_key: IdempotencyKey,
    risk_tier: MutationRiskTier,
    operations: Sequence[PlannedMutationOperation],
    semantic_diff: Sequence[SemanticDiffEntry],
) -> dict[str, object]:
    return {
        "request_id": str(request_id),
        "change_request_sha256": str(change_request_sha256),
        "workspace_id": str(workspace_id),
        "before_revision_id": str(before_revision_id),
        "before_workspace_revision_sha256": str(before_workspace_revision_sha256),
        "before_manifest_sha256": str(before_manifest_sha256),
        "policy_bundle_sha256": str(policy_bundle_sha256),
        "risk_policy_version": risk_policy_version,
        "idempotency_key": str(idempotency_key),
        "risk_tier": risk_tier.value,
        "operations": [_planned_mapping(operation) for operation in operations],
        "semantic_diff": [_semantic_diff_mapping(item) for item in semantic_diff],
    }


def build_mutation_plan(
    request: ChangeRequest,
    *,
    before_workspace_revision_sha256: str | HashDigest,
    policy_bundle_sha256: str | HashDigest,
) -> MutationPlan:
    """Create a stable plan from a request whose current facts were verified."""

    if not isinstance(request, ChangeRequest):
        raise MutationPlanError(
            "mutation.request.type",
            "request must be a ChangeRequest",
        )
    request_id = _opaque(str(request.request_id), "request_id")
    workspace_id = _opaque(str(request.workspace_id), "workspace_id")
    _opaque(str(request.requester_id), "requester_id")
    _timestamp(request.requested_at, "requested_at")
    before_revision = _opaque(str(request.before_revision_id), "before_revision_id")
    before_manifest = _sha256(
        str(request.before_manifest_sha256), "before_manifest_sha256"
    )
    revision_digest = _sha256(
        str(before_workspace_revision_sha256),
        "before_workspace_revision_sha256",
    )
    policy_digest = _sha256(str(policy_bundle_sha256), "policy_bundle_sha256")
    idempotency = IdempotencyKey(
        str(_opaque(str(request.idempotency_key), "idempotency_key"))
    )
    try:
        declared_risk_tier = MutationRiskTier(request.risk_tier)
    except ValueError as error:
        raise MutationPlanError(
            "mutation.request.risk_tier",
            "risk_tier is unsupported",
        ) from error
    if not request.operations:
        raise MutationPlanError(
            "mutation.request.operations_empty",
            "change request must contain at least one operation",
        )
    intents = tuple(
        _validate_intent(operation, index=index)
        for index, operation in enumerate(request.operations)
    )
    _require_nonoverlapping_paths(intents)
    _require_consistent_content_objects(intents)
    canonical_intents = tuple(
        sorted(
            intents,
            key=lambda item: (
                str(item.path),
                item.kind.value,
                str(item.destination_path or ""),
            ),
        )
    )
    risk_tier = _effective_risk_tier(declared_risk_tier, canonical_intents)
    planned: list[PlannedMutationOperation] = []
    for operation in canonical_intents:
        identity = {
            "request_id": str(request_id),
            "operation": _intent_mapping(operation),
        }
        operation_id = OpaqueId(
            f"op-{str(canonical_sha256(identity))[:24]}"
        )
        planned.append(
            PlannedMutationOperation(
                operation_id=operation_id,
                kind=operation.kind,
                path=operation.path,
                expected_before=operation.expected_before,
                new_content=operation.new_content,
                destination_path=operation.destination_path,
                rollback_strategy=_ROLLBACK,
            )
        )
    semantic_diff = tuple(
        SemanticDiffEntry(
            operation_id=operation.operation_id,
            kind=operation.kind,
            path=operation.path,
            destination_path=operation.destination_path,
            before_sha256=operation.expected_before.exact_sha256,
            after_sha256=(
                operation.new_content.exact_sha256
                if operation.new_content is not None
                else (
                    operation.expected_before.exact_sha256
                    if operation.kind is MutationKind.MOVE
                    else None
                )
            ),
        )
        for operation in planned
    )
    request_digest = canonical_sha256(_change_request_mapping(request))
    projection = _plan_projection(
        request_id=request_id,
        change_request_sha256=request_digest,
        workspace_id=workspace_id,
        before_revision_id=before_revision,
        before_workspace_revision_sha256=revision_digest,
        before_manifest_sha256=before_manifest,
        policy_bundle_sha256=policy_digest,
        risk_policy_version=MANAGED_MUTATION_RISK_POLICY_VERSION,
        idempotency_key=idempotency,
        risk_tier=risk_tier,
        operations=planned,
        semantic_diff=semantic_diff,
    )
    digest = canonical_sha256(projection)
    return MutationPlan(
        plan_id=OpaqueId(f"plan-{str(digest)[:24]}"),
        plan_sha256=digest,
        request_id=request_id,
        change_request_sha256=request_digest,
        workspace_id=workspace_id,
        before_revision_id=before_revision,
        before_workspace_revision_sha256=revision_digest,
        before_manifest_sha256=before_manifest,
        policy_bundle_sha256=policy_digest,
        risk_policy_version=MANAGED_MUTATION_RISK_POLICY_VERSION,
        idempotency_key=idempotency,
        risk_tier=risk_tier,
        operations=tuple(planned),
        semantic_diff=semantic_diff,
    )


def plan_mutation(
    request: ChangeRequest,
    base_revision: WorkspaceRevision,
    observation: WorkspaceObservation,
    *,
    policy_bundle_sha256: str | HashDigest,
) -> MutationPlan:
    """Plan only after deriving exact-before facts from a trusted revision.

    Caller-supplied expected digests are treated as assertions, never as the
    source of truth.  The immutable base revision and a complete current
    observation must independently agree before a ready plan is returned.
    """

    if not isinstance(base_revision, WorkspaceRevision):
        raise MutationPlanError(
            "mutation.plan.base_revision_type",
            "base_revision must be a WorkspaceRevision",
        )
    if not isinstance(observation, WorkspaceObservation) or observation.complete is not True:
        raise MutationPlanError(
            "mutation.plan.observation_incomplete",
            "planning requires a complete workspace observation",
        )
    if (
        base_revision.trust_state is not WorkspaceTrustState.TRUSTED
        or observation.trust_state is not WorkspaceTrustState.TRUSTED
    ):
        raise MutationPlanError(
            "mutation.plan.workspace_untrusted",
            "planning is blocked while the workspace is untrusted",
        )
    if (
        request.workspace_id != base_revision.workspace_id
        or observation.workspace_id != base_revision.workspace_id
    ):
        raise MutationPlanError(
            "mutation.plan.workspace_mismatch",
            "request, revision, and observation must share a workspace",
        )
    if (
        request.before_revision_id != base_revision.revision_id
        or observation.revision_id != base_revision.revision_id
    ):
        raise MutationPlanError(
            "mutation.plan.revision_mismatch",
            "request or observation is bound to another revision",
        )
    if (
        request.before_manifest_sha256 != base_revision.manifest_sha256
        or observation.manifest_sha256 != base_revision.manifest_sha256
    ):
        raise MutationPlanError(
            "mutation.plan.manifest_mismatch",
            "request or observation is bound to another manifest",
        )
    try:
        observed = observation_index(observation.entries)
    except MutationPathError as error:
        raise MutationPlanError(error.reason_code, str(error)) from error
    latest: dict[str, object] = {}
    for entry in base_revision.entries:
        path = str(require_managed_path(entry.path))
        existing = latest.get(path)
        if existing is None or entry.revision_ordinal > existing.revision_ordinal:  # type: ignore[attr-defined]
            latest[path] = entry
    active = {
        path: entry
        for path, entry in latest.items()
        if not entry.tombstone  # type: ignore[attr-defined]
    }
    active_aliases: dict[str, str] = {}
    for path in active:
        alias = path_collision_key(path)
        owner = active_aliases.get(alias)
        if owner is not None and owner != path:
            raise MutationPlanError(
                "mutation.path.collision_existing",
                f"base revision contains colliding paths {owner!r} and {path!r}",
            )
        active_aliases[alias] = path
    observed_aliases = {path_collision_key(path): path for path in observed}
    validated_operations = tuple(
        _validate_intent(raw_operation, index=index)
        for index, raw_operation in enumerate(request.operations)
    )
    _require_nonoverlapping_paths(validated_operations)
    for operation in validated_operations:
        try:
            require_no_link_or_reparse_ancestor(operation.path, observed)
            if operation.destination_path is not None:
                require_no_link_or_reparse_ancestor(
                    operation.destination_path,
                    observed,
                )
        except MutationPathError as error:
            raise MutationPlanError(error.reason_code, str(error)) from error
        base_entry = active.get(str(operation.path))
        observed_entry = observed.get(str(operation.path))
        if operation.kind is MutationKind.CREATE:
            if base_entry is not None or observed_entry is not None:
                raise MutationPlanError(
                    "mutation.precondition.create_exists",
                    f"create target already exists: {operation.path}",
                )
            if (
                path_collision_key(operation.path) in active_aliases
                or path_collision_key(operation.path) in observed_aliases
            ):
                raise MutationPlanError(
                    "mutation.path.collision_existing",
                    f"create target aliases an existing path: {operation.path}",
                )
            continue
        if base_entry is None or observed_entry is None:
            raise MutationPlanError(
                "mutation.precondition.source_missing",
                f"mutation source is missing: {operation.path}",
            )
        if observed_entry.node_kind is not PathNodeKind.FILE:
            raise MutationPlanError(
                "mutation.precondition.source_type",
                f"mutation source is not a regular file: {operation.path}",
            )
        expected_digest = base_entry.content_sha256  # type: ignore[attr-defined]
        if (
            expected_digest is None
            or operation.expected_before.exact_sha256 != expected_digest
            or observed_entry.exact_sha256 != expected_digest
        ):
            raise MutationPlanError(
                "mutation.precondition.exact_before_mismatch",
                f"exact-before evidence disagrees: {operation.path}",
            )
        if operation.kind is MutationKind.MOVE:
            assert operation.destination_path is not None
            destination = str(operation.destination_path)
            if destination in active or destination in observed:
                raise MutationPlanError(
                    "mutation.precondition.move_destination_exists",
                    f"move destination already exists: {operation.destination_path}",
                )
            if (
                path_collision_key(operation.destination_path) in active_aliases
                or path_collision_key(operation.destination_path) in observed_aliases
            ):
                raise MutationPlanError(
                    "mutation.path.collision_existing",
                    "move destination aliases an existing path: "
                    f"{operation.destination_path}",
                )
    for path, entry in active.items():
        observed_entry = observed.get(path)
        if (
            observed_entry is None
            or observed_entry.node_kind is not PathNodeKind.FILE
            or observed_entry.exact_sha256 != entry.content_sha256  # type: ignore[attr-defined]
            or observed_entry.byte_length != entry.byte_length  # type: ignore[attr-defined]
        ):
            raise MutationPlanError(
                "mutation.plan.workspace_drift",
                f"complete observation disagrees with the base revision: {path}",
            )
    for path, observed_entry in observed.items():
        if observed_entry.node_kind is PathNodeKind.DIRECTORY:
            continue
        if path not in active:
            raise MutationPlanError(
                "mutation.plan.workspace_drift",
                f"complete observation contains unmanaged content: {path}",
            )
    revision_digest = canonical_sha256(workspace_revision_to_mapping(base_revision))
    return build_mutation_plan(
        request,
        before_workspace_revision_sha256=revision_digest,
        policy_bundle_sha256=policy_bundle_sha256,
    )


def validate_mutation_plan(plan: MutationPlan) -> MutationPlan:
    """Recompute every operation ID and the complete plan identity."""

    if not isinstance(plan, MutationPlan) or not plan.operations:
        raise MutationPlanError("mutation.plan.invalid", "mutation plan is invalid")
    intents = tuple(
        _validate_intent(
            MutationOperationIntent(
                operation.kind,
                operation.path,
                operation.expected_before,
                operation.new_content,
                operation.destination_path,
            ),
            index=index,
        )
        for index, operation in enumerate(plan.operations)
    )
    _require_nonoverlapping_paths(intents)
    _require_consistent_content_objects(intents)
    expected_order = tuple(
        sorted(
            plan.operations,
            key=lambda item: (
                str(item.path), item.kind.value, str(item.destination_path or "")
            ),
        )
    )
    if tuple(plan.operations) != expected_order:
        raise MutationPlanError(
            "mutation.plan.order",
            "mutation plan operations are not in canonical order",
        )
    request_id = _opaque(str(plan.request_id), "request_id")
    change_request_digest = _sha256(
        str(plan.change_request_sha256), "change_request_sha256"
    )
    workspace_id = _opaque(str(plan.workspace_id), "workspace_id")
    before_revision = _opaque(str(plan.before_revision_id), "before_revision_id")
    revision_digest = _sha256(
        str(plan.before_workspace_revision_sha256),
        "before_workspace_revision_sha256",
    )
    before_manifest = _sha256(
        str(plan.before_manifest_sha256), "before_manifest_sha256"
    )
    policy_digest = _sha256(str(plan.policy_bundle_sha256), "policy_bundle_sha256")
    if plan.risk_policy_version != MANAGED_MUTATION_RISK_POLICY_VERSION:
        raise MutationPlanError(
            "mutation.plan.risk_policy_version",
            "mutation plan uses an unsupported risk policy",
        )
    idempotency = IdempotencyKey(
        str(_opaque(str(plan.idempotency_key), "idempotency_key"))
    )
    for operation in plan.operations:
        identity = {
            "request_id": str(request_id),
            "operation": _intent_mapping(
                MutationOperationIntent(
                    operation.kind,
                    operation.path,
                    operation.expected_before,
                    operation.new_content,
                    operation.destination_path,
                )
            ),
        }
        expected_id = f"op-{str(canonical_sha256(identity))[:24]}"
        if str(operation.operation_id) != expected_id:
            raise MutationPlanError(
                "mutation.plan.operation_id",
                "operation_id does not match canonical operation identity",
            )
        if operation.rollback_strategy != _ROLLBACK:
            raise MutationPlanError(
                "mutation.plan.rollback",
                "rollback strategy is unsupported",
            )
    expected_semantic_diff = tuple(
        SemanticDiffEntry(
            operation_id=operation.operation_id,
            kind=operation.kind,
            path=operation.path,
            destination_path=operation.destination_path,
            before_sha256=operation.expected_before.exact_sha256,
            after_sha256=(
                operation.new_content.exact_sha256
                if operation.new_content is not None
                else (
                    operation.expected_before.exact_sha256
                    if operation.kind is MutationKind.MOVE
                    else None
                )
            ),
        )
        for operation in plan.operations
    )
    if plan.semantic_diff != expected_semantic_diff:
        raise MutationPlanError(
            "mutation.plan.semantic_diff",
            "semantic diff does not exactly describe the planned operations",
        )
    try:
        effective_risk = MutationRiskTier(plan.risk_tier)
    except ValueError as error:
        raise MutationPlanError(
            "mutation.plan.risk_tier", "mutation plan risk tier is invalid"
        ) from error
    if _RISK_RANK[effective_risk] < _RISK_RANK[_minimum_risk_tier(intents)]:
        raise MutationPlanError(
            "mutation.plan.risk_downgrade",
            "mutation plan risk tier is below the non-downgradable policy floor",
        )
    projection = _plan_projection(
        request_id=request_id,
        change_request_sha256=change_request_digest,
        workspace_id=workspace_id,
        before_revision_id=before_revision,
        before_workspace_revision_sha256=revision_digest,
        before_manifest_sha256=before_manifest,
        policy_bundle_sha256=policy_digest,
        risk_policy_version=plan.risk_policy_version,
        idempotency_key=idempotency,
        risk_tier=effective_risk,
        operations=plan.operations,
        semantic_diff=plan.semantic_diff,
    )
    digest = canonical_sha256(projection)
    if plan.plan_sha256 != digest or str(plan.plan_id) != f"plan-{str(digest)[:24]}":
        raise MutationPlanError(
            "mutation.plan.identity",
            "plan ID or digest does not match canonical plan identity",
        )
    return plan


def change_request_to_mapping(request: ChangeRequest) -> dict[str, object]:
    # Exercise all request and operation invariants without observing a workspace.
    build_mutation_plan(
        request,
        before_workspace_revision_sha256="0" * 64,
        policy_bundle_sha256="0" * 64,
    )
    return _change_request_mapping(request)


def mutation_plan_to_mapping(plan: MutationPlan) -> dict[str, object]:
    validate_mutation_plan(plan)
    return {
        "artifact_version": "mutation-plan/1.0",
        "plan_id": str(plan.plan_id),
        "plan_sha256": str(plan.plan_sha256),
        **_plan_projection(
            request_id=plan.request_id,
            change_request_sha256=plan.change_request_sha256,
            workspace_id=plan.workspace_id,
            before_revision_id=plan.before_revision_id,
            before_workspace_revision_sha256=plan.before_workspace_revision_sha256,
            before_manifest_sha256=plan.before_manifest_sha256,
            policy_bundle_sha256=plan.policy_bundle_sha256,
            risk_policy_version=plan.risk_policy_version,
            idempotency_key=plan.idempotency_key,
            risk_tier=plan.risk_tier,
            operations=plan.operations,
            semantic_diff=plan.semantic_diff,
        ),
    }


def _operation_result_mapping(result: OperationResult) -> dict[str, object]:
    return {
        "operation_id": str(result.operation_id),
        "kind": result.kind.value,
        "path": str(result.path),
        "destination_path": (
            str(result.destination_path)
            if result.destination_path is not None
            else None
        ),
        "outcome": result.outcome.value,
        "before_sha256": (
            str(result.before_sha256)
            if result.before_sha256 is not None
            else None
        ),
        "after_sha256": (
            str(result.after_sha256)
            if result.after_sha256 is not None
            else None
        ),
        "after_byte_length": result.after_byte_length,
        "reason_code": result.reason_code,
    }


def _validate_artifact_reference(
    reference: ArtifactReference,
    label: str,
) -> ArtifactReference:
    if not isinstance(reference, ArtifactReference):
        raise MutationPlanError(
            "mutation.contract.artifact_reference",
            f"{label} must be an artifact reference",
        )
    try:
        require_managed_path(reference.path, label=f"{label}.path")
    except MutationPathError as error:
        raise MutationPlanError(error.reason_code, str(error)) from error
    _sha256(str(reference.sha256), f"{label}.sha256")
    if not isinstance(reference.artifact_version, str) or not str(
        reference.artifact_version
    ):
        raise MutationPlanError(
            "mutation.contract.artifact_version",
            f"{label}.artifact_version must be non-empty",
        )
    return reference


def _receipt_identity_mapping(receipt: MutationReceipt) -> dict[str, object]:
    return {
        "artifact_version": "mutation-receipt/1.0",
        "plan_id": str(receipt.plan_id),
        "plan_sha256": str(receipt.plan_sha256),
        "workspace_id": str(receipt.workspace_id),
        "idempotency_key": str(receipt.idempotency_key),
        "executor_identity": str(receipt.executor_identity),
        "execution_authorization_id": str(receipt.execution_authorization_id),
        "execution_authorization_sha256": str(
            receipt.execution_authorization_sha256
        ),
        "before_workspace_observation_sha256": str(
            receipt.before_workspace_observation_sha256
        ),
        "after_workspace_observation_sha256": str(
            receipt.after_workspace_observation_sha256
        ),
        "idempotency_reservation": _artifact_mapping(
            receipt.idempotency_reservation
        ),
        "journal_record": _artifact_mapping(receipt.journal_record),
        "started_at": receipt.started_at,
        "completed_at": receipt.completed_at,
        "status": receipt.status.value,
        "before_manifest_sha256": str(receipt.before_manifest_sha256),
        "after_manifest_sha256": str(receipt.after_manifest_sha256),
        "operation_results": [
            _operation_result_mapping(result) for result in receipt.operation_results
        ],
        "rollback_or_reconciliation_required": (
            receipt.rollback_or_reconciliation_required
        ),
        "rollback_or_reconciliation_plan": (
            _artifact_mapping(receipt.rollback_or_reconciliation_plan)
            if receipt.rollback_or_reconciliation_plan is not None
            else None
        ),
    }


def mutation_receipt_id(receipt: MutationReceipt) -> OpaqueId:
    digest = canonical_sha256(_receipt_identity_mapping(receipt))
    return OpaqueId(f"receipt-{str(digest)[:24]}")


def validate_mutation_receipt(receipt: MutationReceipt) -> MutationReceipt:
    """Validate receipt-local invariants without pretending to execute a plan."""

    if not isinstance(receipt, MutationReceipt) or not receipt.operation_results:
        raise MutationPlanError(
            "mutation.receipt.invalid", "mutation receipt is invalid"
        )
    _opaque(str(receipt.receipt_id), "receipt_id")
    _opaque(str(receipt.plan_id), "plan_id")
    _sha256(str(receipt.plan_sha256), "plan_sha256")
    _opaque(str(receipt.workspace_id), "workspace_id")
    _opaque(str(receipt.idempotency_key), "idempotency_key")
    _opaque(str(receipt.executor_identity), "executor_identity")
    _opaque(
        str(receipt.execution_authorization_id),
        "execution_authorization_id",
    )
    _sha256(
        str(receipt.execution_authorization_sha256),
        "execution_authorization_sha256",
    )
    _sha256(
        str(receipt.before_workspace_observation_sha256),
        "before_workspace_observation_sha256",
    )
    _sha256(
        str(receipt.after_workspace_observation_sha256),
        "after_workspace_observation_sha256",
    )
    _validate_artifact_reference(
        receipt.idempotency_reservation,
        "idempotency_reservation",
    )
    _validate_artifact_reference(receipt.journal_record, "journal_record")
    if receipt.rollback_or_reconciliation_plan is not None:
        _validate_artifact_reference(
            receipt.rollback_or_reconciliation_plan,
            "rollback_or_reconciliation_plan",
        )
    if receipt.idempotency_reservation == receipt.journal_record:
        raise MutationPlanError(
            "mutation.receipt.evidence_alias",
            "idempotency reservation and journal record must be distinct",
        )
    started = parse_rfc3339_datetime(_timestamp(receipt.started_at, "started_at"))
    completed = parse_rfc3339_datetime(
        _timestamp(receipt.completed_at, "completed_at")
    )
    if completed < started:
        raise MutationPlanError(
            "mutation.receipt.time_order",
            "receipt completion precedes its start",
        )
    _sha256(str(receipt.before_manifest_sha256), "before_manifest_sha256")
    _sha256(str(receipt.after_manifest_sha256), "after_manifest_sha256")
    try:
        status = MutationReceiptStatus(receipt.status)
    except ValueError as error:
        raise MutationPlanError(
            "mutation.receipt.status", "receipt status is unsupported"
        ) from error

    operation_ids: set[str] = set()
    all_applied = True
    any_applied = False
    all_rejected = True
    for index, result in enumerate(receipt.operation_results):
        operation_id = str(_opaque(str(result.operation_id), "operation_id"))
        if operation_id in operation_ids:
            raise MutationPlanError(
                "mutation.receipt.duplicate_operation",
                "receipt operation IDs must be unique",
            )
        operation_ids.add(operation_id)
        try:
            kind = MutationKind(result.kind)
            outcome = OperationOutcome(result.outcome)
            path = require_managed_path(result.path)
            destination = (
                require_managed_path(result.destination_path)
                if result.destination_path is not None
                else None
            )
        except (ValueError, MutationPathError) as error:
            raise MutationPlanError(
                "mutation.receipt.operation", f"invalid receipt operation {index}"
            ) from error
        before = (
            _sha256(str(result.before_sha256), "before_sha256")
            if result.before_sha256 is not None
            else None
        )
        after = (
            _sha256(str(result.after_sha256), "after_sha256")
            if result.after_sha256 is not None
            else None
        )
        length = result.after_byte_length
        if length is not None and (
            not isinstance(length, int) or isinstance(length, bool) or length < 0
        ):
            raise MutationPlanError(
                "mutation.receipt.byte_length", "receipt byte length is invalid"
            )
        if not isinstance(result.reason_code, str) or not result.reason_code:
            raise MutationPlanError(
                "mutation.receipt.reason", "receipt reason code is required"
            )
        if outcome is OperationOutcome.APPLIED:
            if kind is MutationKind.CREATE:
                shape_ok = (
                    before is None
                    and destination is None
                    and after is not None
                    and length is not None
                )
            elif kind is MutationKind.REPLACE:
                shape_ok = (
                    before is not None
                    and destination is None
                    and after is not None
                    and length is not None
                )
            elif kind is MutationKind.DELETE:
                shape_ok = (
                    before is not None
                    and destination is None
                    and after is None
                    and length is None
                )
            else:
                shape_ok = (
                    before is not None
                    and destination is not None
                    and after == before
                    and length is not None
                )
        else:
            shape_ok = (
                (kind is MutationKind.CREATE and before is None and destination is None)
                or (
                    kind in {MutationKind.REPLACE, MutationKind.DELETE}
                    and before is not None
                    and destination is None
                )
                or (
                    kind is MutationKind.MOVE
                    and before is not None
                    and destination is not None
                )
            )
            shape_ok = shape_ok and after is None and length is None
        if not shape_ok:
            raise MutationPlanError(
                "mutation.receipt.operation_shape",
                f"receipt result does not match {kind.value} semantics for {path}",
            )
        all_applied = all_applied and outcome is OperationOutcome.APPLIED
        any_applied = any_applied or outcome is OperationOutcome.APPLIED
        all_rejected = all_rejected and outcome is OperationOutcome.REJECTED
    if status is MutationReceiptStatus.SUCCEEDED:
        if (
            not all_applied
            or receipt.rollback_or_reconciliation_required
            or receipt.rollback_or_reconciliation_plan is not None
        ):
            raise MutationPlanError(
                "mutation.receipt.success_inconsistent",
                "successful receipt requires all operations applied and no recovery plan",
            )
    elif status is MutationReceiptStatus.REJECTED:
        if (
            not all_rejected
            or receipt.after_manifest_sha256 != receipt.before_manifest_sha256
            or receipt.rollback_or_reconciliation_required
            or receipt.rollback_or_reconciliation_plan is not None
        ):
            raise MutationPlanError(
                "mutation.receipt.rejected_inconsistent",
                "rejected receipt must prove no change and require no recovery",
            )
    elif status is MutationReceiptStatus.RECONCILED:
        if (
            receipt.rollback_or_reconciliation_required
            or receipt.rollback_or_reconciliation_plan is None
        ):
            raise MutationPlanError(
                "mutation.receipt.reconciled_inconsistent",
                "reconciled receipt requires terminal reconciliation evidence",
            )
    elif (
        receipt.rollback_or_reconciliation_required is not True
        or receipt.rollback_or_reconciliation_plan is None
        or (
            status is MutationReceiptStatus.PARTIALLY_APPLIED
            and (not any_applied or all_applied)
        )
        or (status is MutationReceiptStatus.FAILED and all_applied)
    ):
        raise MutationPlanError(
            "mutation.receipt.failure_inconsistent",
            "nonterminal receipt must require a concrete rollback or reconciliation plan",
        )
    if receipt.receipt_id != mutation_receipt_id(receipt):
        raise MutationPlanError(
            "mutation.receipt.identity",
            "receipt ID does not match its complete canonical content",
        )
    return receipt


def mutation_receipt_to_mapping(receipt: MutationReceipt) -> dict[str, object]:
    validate_mutation_receipt(receipt)
    return {
        "receipt_id": str(receipt.receipt_id),
        **_receipt_identity_mapping(receipt),
    }


def validate_workspace_revision(revision: WorkspaceRevision) -> WorkspaceRevision:
    """Validate immutable lineage, content shape, aliases, and identifiers."""

    if not isinstance(revision, WorkspaceRevision):
        raise MutationPlanError(
            "mutation.revision.type", "workspace revision has an invalid type"
        )
    _opaque(str(revision.revision_id), "revision_id")
    _opaque(str(revision.workspace_id), "workspace_id")
    if revision.parent_revision_id is not None:
        _opaque(str(revision.parent_revision_id), "parent_revision_id")
    if not isinstance(revision.origin, WorkspaceRevisionOrigin):
        raise MutationPlanError(
            "mutation.revision.origin", "workspace revision origin is invalid"
        )
    if revision.origin is WorkspaceRevisionOrigin.RECONCILED_BASELINE:
        if revision.parent_revision_id is not None or revision.reconciliation_evidence is None:
            raise MutationPlanError(
                "mutation.revision.baseline_provenance",
                "reconciled baseline requires evidence and cannot have a parent",
            )
        _validate_artifact_reference(
            revision.reconciliation_evidence,
            "reconciliation_evidence",
        )
    elif (
        revision.parent_revision_id is None
        or revision.reconciliation_evidence is not None
    ):
        raise MutationPlanError(
            "mutation.revision.managed_provenance",
            "managed revisions require a parent and cannot claim baseline evidence",
        )
    _sha256(str(revision.manifest_sha256), "manifest_sha256")
    _timestamp(revision.created_at, "created_at")
    _opaque(str(revision.plan_id), "plan_id")
    _opaque(str(revision.receipt_id), "receipt_id")
    if not isinstance(revision.trust_state, WorkspaceTrustState):
        raise MutationPlanError(
            "mutation.revision.trust_state", "workspace trust state is invalid"
        )
    if not isinstance(revision.entries, tuple):
        raise MutationPlanError(
            "mutation.revision.entries", "workspace revision entries must be a tuple"
        )

    alias_owners: dict[str, str] = {}
    ordinals: dict[str, int] = {}
    for index, entry in enumerate(revision.entries):
        if not isinstance(entry, RevisionEntry):
            raise MutationPlanError(
                "mutation.revision.entry_type",
                f"entries[{index}] is not a revision entry",
            )
        try:
            path = require_managed_path(entry.path, label=f"entries[{index}].path")
        except MutationPathError as error:
            raise MutationPlanError(error.reason_code, str(error)) from error
        alias = path_collision_key(path)
        owner = alias_owners.get(alias)
        if owner is not None and owner != str(path):
            raise MutationPlanError(
                "mutation.path.collision_existing",
                f"revision paths collide: {owner!r} and {str(path)!r}",
            )
        alias_owners[alias] = str(path)
        if (
            not isinstance(entry.revision_ordinal, int)
            or isinstance(entry.revision_ordinal, bool)
            or entry.revision_ordinal != ordinals.get(str(path), 0) + 1
        ):
            raise MutationPlanError(
                "mutation.revision.ordinal",
                f"revision ordinals must be contiguous for {path}",
            )
        ordinals[str(path)] = entry.revision_ordinal
        _opaque(str(entry.source_operation_id), "source_operation_id")
        if not isinstance(entry.tombstone, bool):
            raise MutationPlanError(
                "mutation.revision.tombstone", "tombstone must be boolean"
            )
        if entry.tombstone:
            if entry.content_sha256 is not None or entry.byte_length is not None:
                raise MutationPlanError(
                    "mutation.revision.tombstone_content",
                    "tombstones cannot retain content metadata",
                )
        else:
            if entry.content_sha256 is None:
                raise MutationPlanError(
                    "mutation.revision.content_digest",
                    "active revision entries require an exact digest",
                )
            _sha256(str(entry.content_sha256), "content_sha256")
            if (
                not isinstance(entry.byte_length, int)
                or isinstance(entry.byte_length, bool)
                or entry.byte_length < 0
            ):
                raise MutationPlanError(
                    "mutation.revision.byte_length",
                    "active revision entries require a non-negative byte length",
                )
    return revision


def workspace_revision_to_mapping(revision: WorkspaceRevision) -> dict[str, object]:
    validate_workspace_revision(revision)
    return {
        "artifact_version": "workspace-revision/1.0",
        "revision_id": str(revision.revision_id),
        "workspace_id": str(revision.workspace_id),
        "origin": revision.origin.value,
        "parent_revision_id": (
            str(revision.parent_revision_id)
            if revision.parent_revision_id is not None
            else None
        ),
        "reconciliation_evidence": (
            _artifact_mapping(revision.reconciliation_evidence)
            if revision.reconciliation_evidence is not None
            else None
        ),
        "manifest_sha256": str(revision.manifest_sha256),
        "created_at": revision.created_at,
        "plan_id": str(revision.plan_id),
        "receipt_id": str(revision.receipt_id),
        "trust_state": revision.trust_state.value,
        "entries": [
            {
                "path": str(entry.path),
                "revision_ordinal": entry.revision_ordinal,
                "content_sha256": (
                    str(entry.content_sha256)
                    if entry.content_sha256 is not None
                    else None
                ),
                "byte_length": entry.byte_length,
                "tombstone": entry.tombstone,
                "source_operation_id": str(entry.source_operation_id),
            }
            for entry in revision.entries
        ],
    }


def validate_drift_report(report: DriftReport) -> DriftReport:
    """Validate fail-closed flags, ordered findings, and deterministic ID."""

    if not isinstance(report, DriftReport):
        raise MutationPlanError(
            "mutation.drift.type", "drift report has an invalid type"
        )
    _opaque(str(report.report_id), "report_id")
    _opaque(str(report.workspace_id), "workspace_id")
    _opaque(str(report.expected_revision_id), "expected_revision_id")
    _sha256(str(report.expected_manifest_sha256), "expected_manifest_sha256")
    _sha256(str(report.observed_manifest_sha256), "observed_manifest_sha256")
    _timestamp(report.detected_at, "detected_at")
    if report.trust_state is not WorkspaceTrustState.UNTRUSTED:
        raise MutationPlanError(
            "mutation.drift.trust_state", "drift reports must be untrusted"
        )
    flags = (
        report.generation_blocked,
        report.publish_blocked,
        report.dependent_plans_invalidated,
        report.authority_invalidated,
        report.qc_invalidated,
        report.reconciliation_required,
    )
    if any(value is not True for value in flags):
        raise MutationPlanError(
            "mutation.drift.fail_closed", "all drift invalidation flags must be true"
        )
    if not isinstance(report.findings, tuple) or not report.findings:
        raise MutationPlanError(
            "mutation.drift.findings", "drift reports require findings"
        )
    normalized: list[DriftFinding] = []
    for index, finding in enumerate(report.findings):
        if not isinstance(finding, DriftFinding):
            raise MutationPlanError(
                "mutation.drift.finding_type",
                f"findings[{index}] is not a drift finding",
            )
        try:
            path = require_managed_path(
                finding.path, label=f"findings[{index}].path"
            )
            kind = DriftKind(finding.kind)
        except (MutationPathError, ValueError) as error:
            raise MutationPlanError(
                getattr(error, "reason_code", "mutation.drift.finding"),
                str(error),
            ) from error
        expected = (
            _sha256(str(finding.expected_sha256), "expected_sha256")
            if finding.expected_sha256 is not None
            else None
        )
        observed = (
            _sha256(str(finding.observed_sha256), "observed_sha256")
            if finding.observed_sha256 is not None
            else None
        )
        if not isinstance(finding.reason_code, str) or not finding.reason_code:
            raise MutationPlanError(
                "mutation.drift.reason", "drift reason code is required"
            )
        normalized.append(
            DriftFinding(path, kind, expected, observed, finding.reason_code)
        )
    ordered = tuple(
        sorted(
            normalized,
            key=lambda item: (str(item.path), item.kind.value, item.reason_code),
        )
    )
    if tuple(report.findings) != ordered or len(set(ordered)) != len(ordered):
        raise MutationPlanError(
            "mutation.drift.order", "drift findings must be canonical and unique"
        )
    identity = {
        "workspace_id": str(report.workspace_id),
        "expected_revision_id": str(report.expected_revision_id),
        "expected_manifest_sha256": str(report.expected_manifest_sha256),
        "observed_manifest_sha256": str(report.observed_manifest_sha256),
        "detected_at": report.detected_at,
        "findings": [
            {
                "path": str(item.path),
                "kind": item.kind.value,
                "expected_sha256": (
                    str(item.expected_sha256)
                    if item.expected_sha256 is not None
                    else None
                ),
                "observed_sha256": (
                    str(item.observed_sha256)
                    if item.observed_sha256 is not None
                    else None
                ),
                "reason_code": item.reason_code,
            }
            for item in ordered
        ],
    }
    expected_id = f"drift-{str(canonical_sha256(identity))[:24]}"
    if str(report.report_id) != expected_id:
        raise MutationPlanError(
            "mutation.drift.identity", "drift report ID does not match its content"
        )
    return report


def drift_report_to_mapping(report: DriftReport) -> dict[str, object]:
    validate_drift_report(report)
    return {
        "artifact_version": "drift-report/1.0",
        "report_id": str(report.report_id),
        "workspace_id": str(report.workspace_id),
        "expected_revision_id": str(report.expected_revision_id),
        "expected_manifest_sha256": str(report.expected_manifest_sha256),
        "observed_manifest_sha256": str(report.observed_manifest_sha256),
        "detected_at": report.detected_at,
        "trust_state": report.trust_state.value,
        "findings": [
            {
                "path": str(finding.path),
                "kind": finding.kind.value,
                "expected_sha256": (
                    str(finding.expected_sha256)
                    if finding.expected_sha256 is not None
                    else None
                ),
                "observed_sha256": (
                    str(finding.observed_sha256)
                    if finding.observed_sha256 is not None
                    else None
                ),
                "reason_code": finding.reason_code,
            }
            for finding in report.findings
        ],
        "generation_blocked": report.generation_blocked,
        "publish_blocked": report.publish_blocked,
        "dependent_plans_invalidated": report.dependent_plans_invalidated,
        "authority_invalidated": report.authority_invalidated,
        "qc_invalidated": report.qc_invalidated,
        "reconciliation_required": report.reconciliation_required,
    }


def _artifact_mapping(reference: ArtifactReference) -> dict[str, str]:
    return {
        "path": str(reference.path),
        "sha256": str(reference.sha256),
        "artifact_version": str(reference.artifact_version),
    }


def _execution_authorization_identity_mapping(
    authorization: MutationExecutionAuthorization,
) -> dict[str, object]:
    return {
        "plan_id": str(authorization.plan_id),
        "plan_sha256": str(authorization.plan_sha256),
        "workspace_id": str(authorization.workspace_id),
        "revision_id": str(authorization.revision_id),
        "workspace_revision_sha256": str(
            authorization.workspace_revision_sha256
        ),
        "manifest_sha256": str(authorization.manifest_sha256),
        "workspace_observation_sha256": str(
            authorization.workspace_observation_sha256
        ),
        "idempotency_key": str(authorization.idempotency_key),
        "idempotency_reservation": _artifact_mapping(
            authorization.idempotency_reservation
        ),
        "content_observation_sha256": str(
            authorization.content_observation_sha256
        ),
        "content_verifications": [
            _artifact_mapping(item) for item in authorization.content_verifications
        ],
        "service_identity": str(authorization.service_identity),
        "evaluated_at": authorization.evaluated_at,
        "gate_context_sha256": str(authorization.gate_context_sha256),
        "authority_decision": (
            _artifact_mapping(authorization.authority_decision)
            if authorization.authority_decision is not None
            else None
        ),
        "break_glass_authorization_id": (
            str(authorization.break_glass_authorization_id)
            if authorization.break_glass_authorization_id is not None
            else None
        ),
        "break_glass_authorization_sha256": (
            str(authorization.break_glass_authorization_sha256)
            if authorization.break_glass_authorization_sha256 is not None
            else None
        ),
        "break_glass_request_sha256": (
            str(authorization.break_glass_request_sha256)
            if authorization.break_glass_request_sha256 is not None
            else None
        ),
        "human_approval_verifications": [
            _artifact_mapping(item)
            for item in authorization.human_approval_verifications
        ],
        "break_glass_evidence_verifications": [
            _artifact_mapping(item)
            for item in authorization.break_glass_evidence_verifications
        ],
    }


def mutation_content_observation_sha256(
    plan: MutationPlan,
    content_verifications: Sequence[ArtifactReference],
) -> HashDigest:
    """Bind planned immutable objects to the resolver evidence set."""

    objects = {
        (
            str(operation.new_content.object_id),
            str(operation.new_content.exact_sha256),
            operation.new_content.byte_length,
        )
        for operation in plan.operations
        if operation.new_content is not None
    }
    evidence = {
        (
            str(reference.path),
            str(reference.sha256),
            str(reference.artifact_version),
        )
        for reference in content_verifications
    }
    return canonical_sha256(
        {
            "content_objects": [
                {
                    "object_id": object_id,
                    "exact_sha256": exact_sha256,
                    "byte_length": byte_length,
                }
                for object_id, exact_sha256, byte_length in sorted(objects)
            ],
            "resolver_evidence": [
                {
                    "path": path,
                    "sha256": sha256,
                    "artifact_version": artifact_version,
                }
                for path, sha256, artifact_version in sorted(evidence)
            ],
        }
    )


def mutation_execution_authorization_id(
    authorization: MutationExecutionAuthorization,
) -> OpaqueId:
    digest = canonical_sha256(_execution_authorization_identity_mapping(authorization))
    return OpaqueId(f"mutation-auth-{str(digest)[:20]}")


def validate_mutation_execution_authorization(
    authorization: MutationExecutionAuthorization,
) -> MutationExecutionAuthorization:
    """Validate standalone shape and identity, without granting plan authority."""

    if not isinstance(authorization, MutationExecutionAuthorization):
        raise MutationPlanError(
            "mutation.authorization.type",
            "mutation execution authorization has an invalid type",
        )
    _opaque(str(authorization.authorization_id), "authorization_id")
    _opaque(str(authorization.plan_id), "plan_id")
    _sha256(str(authorization.plan_sha256), "plan_sha256")
    _opaque(str(authorization.workspace_id), "workspace_id")
    _opaque(str(authorization.revision_id), "revision_id")
    _sha256(
        str(authorization.workspace_revision_sha256),
        "workspace_revision_sha256",
    )
    _sha256(str(authorization.manifest_sha256), "manifest_sha256")
    _sha256(
        str(authorization.workspace_observation_sha256),
        "workspace_observation_sha256",
    )
    _opaque(str(authorization.idempotency_key), "idempotency_key")
    _validate_artifact_reference(
        authorization.idempotency_reservation,
        "idempotency_reservation",
    )
    _sha256(
        str(authorization.content_observation_sha256),
        "content_observation_sha256",
    )
    _opaque(str(authorization.service_identity), "service_identity")
    _timestamp(authorization.evaluated_at, "evaluated_at")
    _sha256(str(authorization.gate_context_sha256), "gate_context_sha256")
    if authorization.authority_decision is None:
        raise MutationPlanError(
            "mutation.authorization.authority_missing",
            "every mutation authorization requires a trusted authority decision",
        )
    _validate_artifact_reference(
        authorization.authority_decision,
        "authority_decision",
    )

    reference_groups = (
        ("content_verifications", authorization.content_verifications),
        (
            "human_approval_verifications",
            authorization.human_approval_verifications,
        ),
        (
            "break_glass_evidence_verifications",
            authorization.break_glass_evidence_verifications,
        ),
    )
    for label, references in reference_groups:
        if not isinstance(references, tuple):
            raise MutationPlanError(
                "mutation.authorization.reference_set",
                f"{label} must be an immutable tuple",
            )
        keys: set[tuple[str, str, str]] = set()
        for index, reference in enumerate(references):
            _validate_artifact_reference(reference, f"{label}[{index}]")
            key = (
                str(reference.path),
                str(reference.sha256),
                str(reference.artifact_version),
            )
            if key in keys:
                raise MutationPlanError(
                    "mutation.authorization.duplicate_reference",
                    f"{label} contains duplicate evidence",
                )
            keys.add(key)

    break_glass_fields = (
        authorization.break_glass_authorization_id,
        authorization.break_glass_authorization_sha256,
        authorization.break_glass_request_sha256,
    )
    if all(item is None for item in break_glass_fields):
        if (
            authorization.human_approval_verifications
            or authorization.break_glass_evidence_verifications
        ):
            raise MutationPlanError(
                "mutation.authorization.break_glass_shape",
                "break-glass verification evidence lacks a bound authorization",
            )
    elif any(item is None for item in break_glass_fields):
        raise MutationPlanError(
            "mutation.authorization.break_glass_shape",
            "break-glass binding fields must be present together",
        )
    else:
        _opaque(
            str(authorization.break_glass_authorization_id),
            "break_glass_authorization_id",
        )
        _sha256(
            str(authorization.break_glass_authorization_sha256),
            "break_glass_authorization_sha256",
        )
        _sha256(
            str(authorization.break_glass_request_sha256),
            "break_glass_request_sha256",
        )
        if (
            len(authorization.human_approval_verifications) != 2
            or len(authorization.break_glass_evidence_verifications) != 3
        ):
            raise MutationPlanError(
                "mutation.authorization.break_glass_evidence",
                "break-glass execution requires two human and three record verifications",
            )

    if authorization.authorization_id != mutation_execution_authorization_id(
        authorization
    ):
        raise MutationPlanError(
            "mutation.authorization.identity",
            "authorization ID does not match its complete canonical content",
        )
    return authorization


def validate_mutation_execution_authorization_for_plan(
    authorization: MutationExecutionAuthorization,
    plan: MutationPlan,
) -> MutationExecutionAuthorization:
    """Require complete authorization evidence for one exact current plan."""

    validate_mutation_plan(plan)
    validate_mutation_execution_authorization(authorization)
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
        raise MutationPlanError(
            "mutation.authorization.plan_binding",
            "execution authorization is not bound to the exact mutation plan",
        )

    content_objects = {
        (
            str(operation.new_content.object_id),
            str(operation.new_content.exact_sha256),
            operation.new_content.byte_length,
        )
        for operation in plan.operations
        if operation.new_content is not None
    }
    content_evidence = authorization.content_verifications
    if content_objects and not content_evidence:
        raise MutationPlanError(
            "mutation.authorization.content_evidence_missing",
            "create/replace authorization lacks trusted content evidence",
        )
    if not content_objects and content_evidence:
        raise MutationPlanError(
            "mutation.authorization.content_evidence_unexpected",
            "delete/move authorization carries unrelated content evidence",
        )
    if len(content_evidence) > len(content_objects):
        raise MutationPlanError(
            "mutation.authorization.content_evidence_count",
            "content evidence exceeds the unique planned content objects",
        )
    expected_content_digest = mutation_content_observation_sha256(
        plan,
        content_evidence,
    )
    if authorization.content_observation_sha256 != expected_content_digest:
        raise MutationPlanError(
            "mutation.authorization.content_binding",
            "content observation digest does not match the plan and evidence",
        )

    break_glass_bound = authorization.break_glass_authorization_id is not None
    if plan.risk_tier is MutationRiskTier.R4:
        if (
            not break_glass_bound
            or len(authorization.human_approval_verifications) != 2
            or len(authorization.break_glass_evidence_verifications) != 3
        ):
            raise MutationPlanError(
                "mutation.authorization.r4_evidence_missing",
                "R4 plan authorization lacks complete break-glass evidence",
            )
    elif (
        break_glass_bound
        or authorization.human_approval_verifications
        or authorization.break_glass_evidence_verifications
    ):
        raise MutationPlanError(
            "mutation.authorization.break_glass_unexpected",
            "non-R4 plan authorization carries break-glass authority",
        )
    return authorization


def mutation_execution_authorization_to_mapping(
    authorization: MutationExecutionAuthorization,
) -> dict[str, object]:
    validate_mutation_execution_authorization(authorization)
    return {
        "authorization_id": str(authorization.authorization_id),
        **_execution_authorization_identity_mapping(authorization),
    }


def mutation_execution_authorization_sha256(
    authorization: MutationExecutionAuthorization,
) -> HashDigest:
    return canonical_sha256(
        mutation_execution_authorization_to_mapping(authorization)
    )


def break_glass_request_mapping(
    authorization: BreakGlassAuthorization,
) -> dict[str, object]:
    """Canonical approval preimage; excludes approval records and outer ID."""

    return {
        "risk_policy_version": MANAGED_MUTATION_RISK_POLICY_VERSION,
        "risk_tier": authorization.risk_tier.value,
        "standing_grant": authorization.standing_grant,
        "plan_sha256": str(authorization.plan_sha256),
        "workspace_id": str(authorization.workspace_id),
        "before_revision_id": str(authorization.before_revision_id),
        "before_manifest_sha256": str(authorization.before_manifest_sha256),
        "issued_at": authorization.issued_at,
        "expires_at": authorization.expires_at,
        "exact_paths": sorted(str(path) for path in authorization.exact_paths),
        "operation_kinds": sorted(
            kind.value for kind in authorization.operation_kinds
        ),
        "pre_change_snapshot": _artifact_mapping(
            authorization.pre_change_snapshot
        ),
        "incident_id": str(authorization.incident_id),
        "incident_record": _artifact_mapping(authorization.incident_record),
        "audit_record": _artifact_mapping(authorization.audit_record),
        "session_id": str(authorization.session_id),
        "executor_identity": str(authorization.executor_identity),
        "reconciliation_required": authorization.reconciliation_required,
        "post_change_validation_required": (
            authorization.post_change_validation_required
        ),
    }


def break_glass_request_sha256(
    authorization: BreakGlassAuthorization,
) -> HashDigest:
    return canonical_sha256(break_glass_request_mapping(authorization))


def _approval_mapping(approval: AuthenticatedHumanApproval) -> dict[str, object]:
    return {
        "principal_id": str(approval.principal_id),
        "actor_kind": "human",
        "break_glass_request_sha256": str(
            approval.break_glass_request_sha256
        ),
        "authentication_evidence": _artifact_mapping(
            approval.authentication_evidence
        ),
        "approval_record": _artifact_mapping(approval.approval_record),
        "approved_at": approval.approved_at,
    }


def break_glass_authorization_to_mapping(
    authorization: BreakGlassAuthorization,
) -> dict[str, object]:
    return {
        "artifact_version": "break-glass-authorization/1.0",
        "authorization_id": str(authorization.authorization_id),
        "risk_tier": authorization.risk_tier.value,
        "standing_grant": authorization.standing_grant,
        "plan_sha256": str(authorization.plan_sha256),
        "workspace_id": str(authorization.workspace_id),
        "before_revision_id": str(authorization.before_revision_id),
        "before_manifest_sha256": str(authorization.before_manifest_sha256),
        "issued_at": authorization.issued_at,
        "expires_at": authorization.expires_at,
        "exact_paths": sorted(str(path) for path in authorization.exact_paths),
        "operation_kinds": sorted(kind.value for kind in authorization.operation_kinds),
        "approvals": [_approval_mapping(item) for item in authorization.approvals],
        "pre_change_snapshot": _artifact_mapping(
            authorization.pre_change_snapshot
        ),
        "incident_id": str(authorization.incident_id),
        "incident_record": _artifact_mapping(authorization.incident_record),
        "audit_record": _artifact_mapping(authorization.audit_record),
        "session_id": str(authorization.session_id),
        "executor_identity": str(authorization.executor_identity),
        "reconciliation_required": authorization.reconciliation_required,
        "post_change_validation_required": (
            authorization.post_change_validation_required
        ),
    }
