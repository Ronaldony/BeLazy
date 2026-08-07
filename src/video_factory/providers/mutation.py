"""Runtime ports and immediate pre-side-effect guard for managed mutation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Protocol

from video_factory.approvals import (
    GateContext,
    gate_context_from_mapping,
    gate_context_sha256,
    gate_context_to_mapping,
)
from video_factory.config.canonical import canonical_sha256
from video_factory.domain import ArtifactReference, OpaqueId
from video_factory.json_boundary import parse_rfc3339_datetime
from video_factory.mutation.contracts import (
    AuthenticatedHumanApproval,
    BreakGlassAuthorization,
    IdempotencyRecord,
    MutationExecutionAuthorization,
    MutationKind,
    MutationPlan,
    MutationReceipt,
    MutationRiskTier,
    PathNodeKind,
    WorkspaceObservation,
    WorkspaceTrustState,
)
from video_factory.mutation.paths import (
    MutationPathError,
    observation_index,
    path_collision_key,
    require_collision_free,
    require_managed_path,
    require_no_link_or_reparse_ancestor,
)
from video_factory.mutation.planner import MutationPlanError, validate_mutation_plan
from video_factory.mutation.trust import workspace_trust_blockers


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OPAQUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class MutationRuntimeError(ValueError):
    """Fail-closed runtime rejection carrying a stable reason code."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class BreakGlassPolicy:
    """Caller-supplied policy limit; core does not invent an expiry window."""

    maximum_validity_seconds: int


class HumanApprovalAuthenticator(Protocol):
    """Trusted identity/ledger boundary; validation never synthesizes a human."""

    def verify_current_human_approval(
        self,
        approval: AuthenticatedHumanApproval,
        *,
        evaluated_at: datetime,
    ) -> bool: ...


class MutationAuthorityVerifier(Protocol):
    """W04-compatible authority port for R2/R3 managed mutations."""

    def verify(
        self,
        plan: MutationPlan,
        observation: WorkspaceObservation,
        *,
        current_context: GateContext,
        evaluated_at: datetime,
    ) -> ArtifactReference | None: ...


class ManagedMutationExecutorPort(Protocol):
    """Actual durable executor is supplied by the separate runtime boundary."""

    def apply(
        self,
        plan: MutationPlan,
        authorization: MutationExecutionAuthorization,
    ) -> MutationReceipt: ...


def _reject(reason_code: str, message: str) -> None:
    raise MutationRuntimeError(reason_code, message)


def _valid_opaque(value: object) -> bool:
    return isinstance(value, str) and _OPAQUE.fullmatch(value) is not None


def _valid_digest(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _validate_reference(reference: ArtifactReference, label: str) -> None:
    if not isinstance(reference, ArtifactReference):
        _reject("mutation.authority.reference", f"{label} must be an artifact reference")
    try:
        require_managed_path(reference.path, label=f"{label}.path")
    except MutationPathError as error:
        _reject(error.reason_code, str(error))
    if not _valid_digest(str(reference.sha256)):
        _reject("mutation.authority.reference_digest", f"{label}.sha256 is invalid")
    if not str(reference.artifact_version):
        _reject(
            "mutation.authority.reference_version",
            f"{label}.artifact_version is empty",
        )


def require_trusted_workspace(
    observation: WorkspaceObservation,
    *,
    expected_workspace_id: OpaqueId | None = None,
    expected_revision_id: OpaqueId,
    expected_manifest_sha256: str,
    capability: str,
) -> None:
    """Shared generation/publish/mutation gate for observed workspace trust."""

    blockers = workspace_trust_blockers(
        observation,
        expected_workspace_id=expected_workspace_id,
        expected_revision_id=expected_revision_id,
        expected_manifest_sha256=expected_manifest_sha256,
    )
    if blockers:
        _reject(blockers[0], f"{capability} workspace trust check failed: {blockers[0]}")


def _validate_approval_reference_set(
    approvals: tuple[AuthenticatedHumanApproval, ...],
) -> None:
    if len(approvals) != 2:
        _reject(
            "mutation.break_glass.approver_count",
            "break-glass requires exactly two human approvals",
        )
    principals = [str(item.principal_id) for item in approvals]
    if any(not _valid_opaque(item) for item in principals) or len(set(principals)) != 2:
        _reject(
            "mutation.break_glass.approver_distinctness",
            "break-glass approvers must be two distinct authenticated principals",
        )
    authentication_refs: set[tuple[str, str, str]] = set()
    approval_refs: set[tuple[str, str, str]] = set()
    for index, approval in enumerate(approvals):
        _validate_reference(
            approval.authentication_evidence,
            f"approvals[{index}].authentication_evidence",
        )
        _validate_reference(
            approval.approval_record,
            f"approvals[{index}].approval_record",
        )
        authentication_refs.add(
            (
                str(approval.authentication_evidence.path),
                str(approval.authentication_evidence.sha256),
                str(approval.authentication_evidence.artifact_version),
            )
        )
        approval_refs.add(
            (
                str(approval.approval_record.path),
                str(approval.approval_record.sha256),
                str(approval.approval_record.artifact_version),
            )
        )
    if len(authentication_refs) != 2 or len(approval_refs) != 2:
        _reject(
            "mutation.break_glass.evidence_distinctness",
            "break-glass approvals require distinct authentication and approval records",
        )


def validate_break_glass_authorization(
    authorization: BreakGlassAuthorization,
    plan: MutationPlan,
    observation: WorkspaceObservation,
    *,
    service_identity: OpaqueId,
    evaluated_at: datetime,
    policy: BreakGlassPolicy,
    authenticator: HumanApprovalAuthenticator,
) -> None:
    """Validate human-created R4 evidence without creating or inferring it."""

    if not isinstance(authorization, BreakGlassAuthorization):
        _reject(
            "mutation.break_glass.missing",
            "R4 mutation requires break-glass authorization",
        )
    if authorization.risk_tier is not MutationRiskTier.R4 or authorization.standing_grant:
        _reject(
            "mutation.break_glass.risk_or_standing",
            "break-glass must be one-shot R4 authority",
        )
    if (
        authorization.reconciliation_required is not True
        or authorization.post_change_validation_required is not True
    ):
        _reject(
            "mutation.break_glass.reconciliation",
            "break-glass must require post-change validation and reconciliation",
        )
    if authorization.plan_sha256 != plan.plan_sha256:
        _reject(
            "mutation.break_glass.plan_mismatch",
            "break-glass authority is bound to another mutation plan",
        )
    if (
        authorization.workspace_id != plan.workspace_id
        or authorization.before_revision_id != plan.before_revision_id
        or authorization.before_manifest_sha256 != plan.before_manifest_sha256
    ):
        _reject(
            "mutation.break_glass.workspace_binding",
            "break-glass authority is bound to another workspace state",
        )
    if authorization.executor_identity != service_identity:
        _reject(
            "mutation.break_glass.executor_mismatch",
            "break-glass authority is bound to another executor identity",
        )
    if policy.maximum_validity_seconds <= 0:
        _reject(
            "mutation.break_glass.policy_invalid",
            "break-glass maximum validity must be positive",
        )
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        _reject(
            "mutation.break_glass.evaluation_time",
            "break-glass evaluation time must be timezone-aware",
        )
    try:
        issued_at = parse_rfc3339_datetime(authorization.issued_at)
        expires_at = parse_rfc3339_datetime(authorization.expires_at)
    except ValueError:
        _reject(
            "mutation.break_glass.validity_window",
            "break-glass validity window is malformed",
        )
    validity_seconds = (expires_at - issued_at).total_seconds()
    if validity_seconds <= 0 or validity_seconds > policy.maximum_validity_seconds:
        _reject(
            "mutation.break_glass.validity_window",
            "break-glass validity window is not narrow enough",
        )
    if evaluated_at < issued_at or evaluated_at >= expires_at:
        _reject(
            "mutation.break_glass.expired_or_future",
            "break-glass authority is not current at evaluation time",
        )

    touched = {
        str(path)
        for operation in plan.operations
        for path in (operation.path, operation.destination_path)
        if path is not None
    }
    try:
        scoped = {
            str(path)
            for path in require_collision_free(
                authorization.exact_paths,
                label="break-glass exact paths",
            )
        }
    except MutationPathError as error:
        _reject(error.reason_code, str(error))
    if scoped != touched:
        _reject(
            "mutation.break_glass.path_scope",
            "break-glass exact path scope does not equal the plan scope",
        )
    if authorization.operation_kinds != frozenset(
        operation.kind for operation in plan.operations
    ):
        _reject(
            "mutation.break_glass.operation_scope",
            "break-glass operation scope does not equal the plan scope",
        )
    _validate_approval_reference_set(authorization.approvals)
    for approval in authorization.approvals:
        try:
            approved_at = parse_rfc3339_datetime(approval.approved_at)
        except ValueError:
            _reject(
                "mutation.break_glass.approval_time",
                "human approval time is malformed",
            )
        if approved_at > issued_at or approved_at > evaluated_at:
            _reject(
                "mutation.break_glass.future_approval",
                "human approval is later than authority issuance or evaluation",
            )
        if not authenticator.verify_current_human_approval(
            approval,
            evaluated_at=evaluated_at,
        ):
            _reject(
                "mutation.break_glass.human_authentication",
                "a break-glass approver is not authenticated as a human",
            )
    _validate_reference(authorization.pre_change_snapshot, "pre_change_snapshot")
    if authorization.pre_change_snapshot.sha256 != observation.manifest_sha256:
        _reject(
            "mutation.break_glass.snapshot_mismatch",
            "pre-change snapshot does not bind the current manifest",
        )
    _validate_reference(authorization.audit_record, "audit_record")
    for label, value in (
        ("authorization_id", authorization.authorization_id),
        ("incident_id", authorization.incident_id),
        ("session_id", authorization.session_id),
    ):
        if not _valid_opaque(str(value)):
            _reject(
                "mutation.break_glass.identifier",
                f"{label} is invalid",
            )


class MutationPreSideEffectGuard:
    """Revalidate every mutable fact immediately before executor delegation."""

    def authorize(
        self,
        plan: MutationPlan,
        observation: WorkspaceObservation,
        *,
        service_identity: OpaqueId,
        current_context: GateContext,
        evaluated_at: datetime,
        kill_switch_engaged: bool = False,
        idempotency_record: IdempotencyRecord | None = None,
        authority_verifier: MutationAuthorityVerifier | None = None,
        break_glass: BreakGlassAuthorization | None = None,
        break_glass_policy: BreakGlassPolicy | None = None,
        human_authenticator: HumanApprovalAuthenticator | None = None,
    ) -> MutationExecutionAuthorization:
        try:
            validate_mutation_plan(plan)
        except MutationPlanError as error:
            _reject(error.reason_code, str(error))
        if not _valid_opaque(str(service_identity)):
            _reject(
                "mutation.runtime.service_identity",
                "runtime service identity is required",
            )
        if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
            _reject(
                "mutation.runtime.evaluation_time",
                "runtime evaluation time must be timezone-aware",
            )
        if not isinstance(kill_switch_engaged, bool) or kill_switch_engaged:
            _reject(
                "mutation.runtime.kill_switch",
                "managed mutation is disabled by the runtime kill switch",
            )
        try:
            normalized_context = gate_context_from_mapping(
                gate_context_to_mapping(current_context)
            )
        except (AttributeError, TypeError, ValueError):
            _reject(
                "mutation.runtime.gate_context",
                "managed mutation requires a valid current gate context",
            )
        if normalized_context.policy_bundle_sha256 != plan.policy_bundle_sha256:
            _reject(
                "mutation.runtime.policy_context_mismatch",
                "mutation plan policy digest differs from current gate context",
            )
        if normalized_context.current_manifest_sha256 != plan.before_manifest_sha256:
            _reject(
                "mutation.runtime.manifest_context_mismatch",
                "mutation plan manifest differs from current gate context",
            )
        if normalized_context.executable_plan_sha256 != plan.plan_sha256:
            _reject(
                "mutation.runtime.plan_context_mismatch",
                "mutation plan digest differs from current gate context",
            )
        require_trusted_workspace(
            observation,
            expected_workspace_id=plan.workspace_id,
            expected_revision_id=plan.before_revision_id,
            expected_manifest_sha256=str(plan.before_manifest_sha256),
            capability="managed mutation",
        )
        if observation.workspace_id != plan.workspace_id:
            _reject(
                "mutation.runtime.workspace_mismatch",
                "plan and observed workspace differ",
            )
        try:
            entries = observation_index(observation.entries)
        except MutationPathError as error:
            _reject(error.reason_code, str(error))
        entry_aliases = {path_collision_key(path): path for path in entries}
        for item in observation.entries:
            if item.node_kind is PathNodeKind.FILE:
                if not _valid_digest(str(item.exact_sha256)):
                    _reject(
                        "mutation.runtime.observed_digest",
                        "observed file is missing an exact digest",
                    )
                if (
                    not isinstance(item.byte_length, int)
                    or isinstance(item.byte_length, bool)
                    or item.byte_length < 0
                ):
                    _reject(
                        "mutation.runtime.observed_length",
                        "observed file has an invalid byte length",
                    )
        for operation in plan.operations:
            try:
                require_no_link_or_reparse_ancestor(operation.path, entries)
                if operation.destination_path is not None:
                    require_no_link_or_reparse_ancestor(
                        operation.destination_path,
                        entries,
                    )
            except MutationPathError as error:
                _reject(error.reason_code, str(error))
            current = entries.get(str(operation.path))
            if operation.kind is MutationKind.CREATE:
                if current is not None:
                    _reject(
                        "mutation.precondition.create_exists",
                        f"create target already exists: {operation.path}",
                    )
                if path_collision_key(operation.path) in entry_aliases:
                    _reject(
                        "mutation.path.collision_existing",
                        f"create target aliases an existing path: {operation.path}",
                    )
                continue
            if current is None or current.node_kind is not PathNodeKind.FILE:
                _reject(
                    "mutation.precondition.source_missing",
                    f"mutation source is not a regular file: {operation.path}",
                )
            if current.exact_sha256 != operation.expected_before.exact_sha256:
                _reject(
                    "mutation.precondition.exact_before_mismatch",
                    f"exact-before digest changed: {operation.path}",
                )
            if operation.kind is MutationKind.MOVE:
                assert operation.destination_path is not None
                if str(operation.destination_path) in entries:
                    _reject(
                        "mutation.precondition.move_destination_exists",
                        f"move destination already exists: {operation.destination_path}",
                    )
                if path_collision_key(operation.destination_path) in entry_aliases:
                    _reject(
                        "mutation.path.collision_existing",
                        "move destination aliases an existing path: "
                        f"{operation.destination_path}",
                    )

        if idempotency_record is not None:
            if idempotency_record.idempotency_key != plan.idempotency_key:
                _reject(
                    "mutation.idempotency.record_key_mismatch",
                    "idempotency record is bound to another key",
                )
            if idempotency_record.plan_sha256 != plan.plan_sha256:
                _reject(
                    "mutation.idempotency.conflict",
                    "the idempotency key is already bound to another plan digest",
                )
            _reject(
                "mutation.idempotency.replay",
                "the exact plan already has an idempotency record; resume or return its receipt",
            )

        authority_decision: ArtifactReference | None = None
        if plan.risk_tier in {MutationRiskTier.R2, MutationRiskTier.R3}:
            if authority_verifier is None:
                _reject(
                    "mutation.authority.verifier_missing",
                    "R2/R3 mutation requires a trusted authority verifier",
                )
            authority_decision = authority_verifier.verify(
                plan,
                observation,
                current_context=normalized_context,
                evaluated_at=evaluated_at,
            )
            if authority_decision is None:
                _reject(
                    "mutation.authority.denied",
                    "mutation authority was not granted",
                )
            _validate_reference(authority_decision, "authority_decision")

        break_glass_id: OpaqueId | None = None
        if plan.risk_tier is MutationRiskTier.R4:
            if (
                break_glass is None
                or break_glass_policy is None
                or human_authenticator is None
            ):
                _reject(
                    "mutation.break_glass.missing",
                    "R4 mutation requires evidence, policy, and human authentication",
                )
            validate_break_glass_authorization(
                break_glass,
                plan,
                observation,
                service_identity=service_identity,
                evaluated_at=evaluated_at,
                policy=break_glass_policy,
                authenticator=human_authenticator,
            )
            break_glass_id = break_glass.authorization_id

        identity = {
            "plan_id": str(plan.plan_id),
            "plan_sha256": str(plan.plan_sha256),
            "workspace_id": str(plan.workspace_id),
            "revision_id": str(observation.revision_id),
            "manifest_sha256": str(observation.manifest_sha256),
            "idempotency_key": str(plan.idempotency_key),
            "service_identity": str(service_identity),
            "evaluated_at": evaluated_at.isoformat(),
            "gate_context_sha256": str(gate_context_sha256(normalized_context)),
            "authority_decision": (
                {
                    "path": str(authority_decision.path),
                    "sha256": str(authority_decision.sha256),
                    "artifact_version": str(authority_decision.artifact_version),
                }
                if authority_decision is not None
                else None
            ),
            "break_glass_authorization_id": (
                str(break_glass_id) if break_glass_id is not None else None
            ),
        }
        digest = canonical_sha256(identity)
        return MutationExecutionAuthorization(
            authorization_id=OpaqueId(f"mutation-auth-{str(digest)[:20]}"),
            plan_id=plan.plan_id,
            plan_sha256=plan.plan_sha256,
            workspace_id=plan.workspace_id,
            revision_id=observation.revision_id,
            manifest_sha256=observation.manifest_sha256,
            idempotency_key=plan.idempotency_key,
            service_identity=service_identity,
            evaluated_at=evaluated_at.isoformat(),
            gate_context_sha256=gate_context_sha256(normalized_context),
            authority_decision=authority_decision,
            break_glass_authorization_id=break_glass_id,
        )
