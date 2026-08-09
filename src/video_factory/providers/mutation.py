"""Runtime ports and immediate pre-side-effect guard for managed mutation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import re
from typing import Protocol

from video_factory.approvals import (
    GateContext,
    gate_context_from_mapping,
    gate_context_sha256,
    gate_context_to_mapping,
)
from video_factory.authority import (
    ActionAuthorityRequest,
    AuthorityContractError,
    AuthorityDecision,
    TrustedAuthorizationLedger,
    VerificationPurpose,
    revalidate_authority_for_side_effect,
)
from video_factory.config.canonical import canonical_sha256
from video_factory.domain import ArtifactReference, HashDigest, OpaqueId
from video_factory.json_boundary import parse_rfc3339_datetime
from video_factory.mutation.contracts import (
    AuthenticatedHumanApproval,
    BreakGlassAuthorization,
    BreakGlassEvidenceVerification,
    ContentObject,
    ContentObjectObservation,
    IdempotencyReservation,
    MutationExecutionAuthorization,
    MutationKind,
    MutationPlan,
    MutationReceipt,
    MutationRiskTier,
    PathNodeKind,
    WorkspaceObservation,
    WorkspaceRevision,
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
from video_factory.mutation.planner import (
    MutationPlanError,
    break_glass_authorization_to_mapping,
    break_glass_request_sha256,
    mutation_content_observation_sha256,
    mutation_execution_authorization_id,
    validate_mutation_plan,
    validate_mutation_execution_authorization_for_plan,
)
from video_factory.mutation.trust import (
    workspace_observation_sha256,
    workspace_trust_blockers,
)


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
    """Trusted current GRANTED/non-revoked human-ledger verification port."""

    def verify_current_human_approval(
        self,
        approval: AuthenticatedHumanApproval,
        authorization: BreakGlassAuthorization,
        *,
        expected_request_sha256: HashDigest,
        current_context: GateContext,
        evaluated_at: datetime,
    ) -> ArtifactReference | None: ...


class BreakGlassEvidenceVerifier(Protocol):
    """Trusted existence/immutability verifier for snapshot/incident/audit."""

    def verify_current_evidence(
        self,
        authorization: BreakGlassAuthorization,
        plan: MutationPlan,
        *,
        expected_request_sha256: HashDigest,
        current_context: GateContext,
        evaluated_at: datetime,
    ) -> BreakGlassEvidenceVerification | None: ...


class MutationAuthorityVerifier(Protocol):
    """W04-compatible trusted authority port required for every mutation."""

    def verify(
        self,
        plan: MutationPlan,
        observation: WorkspaceObservation,
        *,
        current_context: GateContext,
        evaluated_at: datetime,
    ) -> ArtifactReference | None: ...


class MutationContentResolver(Protocol):
    """Trusted resolver for immutable new-content bytes."""

    def resolve_current(
        self,
        content: ContentObject,
        *,
        evaluated_at: datetime,
    ) -> ContentObjectObservation | None: ...


class MutationIdempotencyLedger(Protocol):
    """Trusted atomic lookup/reserve boundary for mutation idempotency."""

    def reserve_current(
        self,
        plan: MutationPlan,
        observation: WorkspaceObservation,
        *,
        workspace_observation_sha256: HashDigest,
        service_identity: OpaqueId,
        evaluated_at: datetime,
    ) -> IdempotencyReservation | None: ...


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
    expected_workspace_id: OpaqueId,
    expected_revision_id: OpaqueId,
    expected_manifest_sha256: str,
    expected_revision: WorkspaceRevision,
    expected_revision_sha256: HashDigest,
    capability: str,
) -> None:
    """Shared generation/publish/mutation gate for observed workspace trust."""

    blockers = workspace_trust_blockers(
        observation,
        expected_workspace_id=expected_workspace_id,
        expected_revision_id=expected_revision_id,
        expected_manifest_sha256=expected_manifest_sha256,
        expected_revision=expected_revision,
        expected_revision_sha256=expected_revision_sha256,
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
    current_context: GateContext,
    policy: BreakGlassPolicy,
    authenticator: HumanApprovalAuthenticator,
    evidence_verifier: BreakGlassEvidenceVerifier,
) -> tuple[
    HashDigest,
    HashDigest,
    tuple[ArtifactReference, ...],
    tuple[ArtifactReference, ...],
]:
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
    request_sha256 = break_glass_request_sha256(authorization)
    _validate_approval_reference_set(authorization.approvals)
    human_verifications: list[ArtifactReference] = []
    for approval in authorization.approvals:
        if approval.break_glass_request_sha256 != request_sha256:
            _reject(
                "mutation.break_glass.approval_binding",
                "human approval is bound to another break-glass request",
            )
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
        verification = authenticator.verify_current_human_approval(
            approval,
            authorization,
            expected_request_sha256=request_sha256,
            current_context=current_context,
            evaluated_at=evaluated_at,
        )
        if verification is None:
            _reject(
                "mutation.break_glass.human_authentication",
                "a human approval is not current, granted, and authenticated",
            )
        _validate_reference(verification, "human_approval_verification")
        human_verifications.append(verification)
    human_verification_keys = {
        (str(item.path), str(item.sha256), str(item.artifact_version))
        for item in human_verifications
    }
    if len(human_verification_keys) != 2:
        _reject(
            "mutation.break_glass.human_verification_distinctness",
            "human approval verification records must be distinct",
        )
    _validate_reference(authorization.pre_change_snapshot, "pre_change_snapshot")
    if authorization.pre_change_snapshot.sha256 != observation.manifest_sha256:
        _reject(
            "mutation.break_glass.snapshot_mismatch",
            "pre-change snapshot does not bind the current manifest",
        )
    _validate_reference(authorization.incident_record, "incident_record")
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
    evidence = evidence_verifier.verify_current_evidence(
        authorization,
        plan,
        expected_request_sha256=request_sha256,
        current_context=current_context,
        evaluated_at=evaluated_at,
    )
    if (
        not isinstance(evidence, BreakGlassEvidenceVerification)
        or evidence.break_glass_request_sha256 != request_sha256
    ):
        _reject(
            "mutation.break_glass.evidence_unverified",
            "snapshot, incident, and audit evidence is not currently verified",
        )
    evidence_verifications = (
        evidence.snapshot_verification,
        evidence.incident_verification,
        evidence.audit_verification,
    )
    for index, reference in enumerate(evidence_verifications):
        _validate_reference(reference, f"break_glass_evidence_verifications[{index}]")
    evidence_keys = {
        (str(item.path), str(item.sha256), str(item.artifact_version))
        for item in evidence_verifications
    }
    if len(evidence_keys) != 3:
        _reject(
            "mutation.break_glass.evidence_verification_distinctness",
            "snapshot, incident, and audit verifications must be distinct",
        )
    authorization_sha256 = canonical_sha256(
        break_glass_authorization_to_mapping(authorization)
    )
    return (
        authorization_sha256,
        request_sha256,
        tuple(human_verifications),
        evidence_verifications,
    )


class MutationPreSideEffectGuard:
    """Revalidate every mutable fact immediately before executor delegation."""

    def authorize(
        self,
        plan: MutationPlan,
        observation: WorkspaceObservation,
        *,
        expected_revision: WorkspaceRevision,
        service_identity: OpaqueId,
        current_context: GateContext,
        evaluated_at: datetime,
        kill_switch_engaged: bool = False,
        authority_verifier: MutationAuthorityVerifier | None = None,
        w04_authority_request: ActionAuthorityRequest | None = None,
        w04_authority_decision: AuthorityDecision | None = None,
        w04_authority_ledger: TrustedAuthorizationLedger | None = None,
        break_glass: BreakGlassAuthorization | None = None,
        break_glass_policy: BreakGlassPolicy | None = None,
        human_authenticator: HumanApprovalAuthenticator | None = None,
        break_glass_evidence_verifier: BreakGlassEvidenceVerifier | None = None,
        content_resolver: MutationContentResolver | None = None,
        idempotency_ledger: MutationIdempotencyLedger | None = None,
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
            expected_revision=expected_revision,
            expected_revision_sha256=plan.before_workspace_revision_sha256,
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

        unique_content: dict[str, ContentObject] = {}
        for operation in plan.operations:
            if operation.new_content is None:
                continue
            content_key = str(operation.new_content.object_id)
            existing_content = unique_content.get(content_key)
            if existing_content is not None:
                if existing_content != operation.new_content:
                    _reject(
                        "mutation.content.object_identity_conflict",
                        "one content object ID is bound to conflicting bytes",
                    )
                continue
            unique_content[content_key] = operation.new_content

        content_observations: list[ContentObjectObservation] = []
        for content in sorted(
            unique_content.values(),
            key=lambda item: (
                str(item.object_id),
                str(item.exact_sha256),
                item.byte_length,
            ),
        ):
            if content_resolver is None:
                _reject(
                    "mutation.content.resolver_missing",
                    "create/replace requires a trusted current content resolver",
                )
            content_observation = content_resolver.resolve_current(
                content,
                evaluated_at=evaluated_at,
            )
            if not isinstance(content_observation, ContentObjectObservation):
                _reject(
                    "mutation.content.unresolved",
                    "planned content could not be resolved to current bytes",
                )
            if (
                content_observation.object_id != content.object_id
                or content_observation.exact_sha256
                != content.exact_sha256
                or content_observation.byte_length != content.byte_length
            ):
                _reject(
                    "mutation.content.mismatch",
                    "current content bytes do not match the exact planned object",
                )
            _validate_reference(
                content_observation.resolver_evidence,
                "content_resolver_evidence",
            )
            content_observations.append(content_observation)
        verification_by_identity = {
            (
                str(item.resolver_evidence.path),
                str(item.resolver_evidence.sha256),
                str(item.resolver_evidence.artifact_version),
            ): item.resolver_evidence
            for item in content_observations
        }
        content_verifications = tuple(
            verification_by_identity[key]
            for key in sorted(verification_by_identity)
        )
        content_observation_digest = mutation_content_observation_sha256(
            tuple(content_observations),
        )

        if authority_verifier is None:
            _reject(
                "mutation.authority.verifier_missing",
                "every mutation requires a trusted authority verifier",
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
        if w04_authority_request is None or w04_authority_decision is None:
            _reject(
                "mutation.authority.w04_missing",
                "managed mutation requires an exact W04 authority decision",
            )
        if w04_authority_ledger is None:
            _reject(
                "mutation.authority.w04_ledger_missing",
                "managed mutation requires the trusted W04 authority ledger",
            )
        if (
            plan.requester_id is None
            or w04_authority_request.action_id != "managed_mutation"
            or w04_authority_request.capability_id != "managed_mutation"
            or w04_authority_request.executable_plan_sha256 != plan.plan_sha256
            or w04_authority_request.gate_context != normalized_context
            or w04_authority_request.scope.workspace_id != plan.workspace_id
            or w04_authority_request.requester_principal_id
            != plan.requester_id
        ):
            _reject(
                "mutation.authority.w04_request_mismatch",
                "W04 authority request is bound to another mutation plan",
            )
        if (
            str(authority_decision.sha256)
            != str(w04_authority_decision.decision_sha256)
            or str(authority_decision.artifact_version)
            != "authority-decision/1.0"
        ):
            _reject(
                "mutation.authority.w04_reference_mismatch",
                "mutation authority reference is not the exact W04 decision",
            )

        break_glass_id: OpaqueId | None = None
        break_glass_authorization_sha256: HashDigest | None = None
        bound_break_glass_request_sha256: HashDigest | None = None
        human_verifications: tuple[ArtifactReference, ...] = ()
        evidence_verifications: tuple[ArtifactReference, ...] = ()
        if plan.risk_tier is MutationRiskTier.R4:
            if (
                break_glass is None
                or break_glass_policy is None
                or human_authenticator is None
                or break_glass_evidence_verifier is None
            ):
                _reject(
                    "mutation.break_glass.missing",
                    "R4 mutation requires evidence, policy, and human authentication",
                )
            if plan.requester_id in {
                approval.principal_id for approval in break_glass.approvals
            }:
                _reject(
                    "mutation.break_glass.self_approval",
                    "the mutation requester cannot approve the break-glass action",
                )
            (
                break_glass_authorization_sha256,
                bound_break_glass_request_sha256,
                human_verifications,
                evidence_verifications,
            ) = validate_break_glass_authorization(
                break_glass,
                plan,
                observation,
                service_identity=service_identity,
                evaluated_at=evaluated_at,
                current_context=normalized_context,
                policy=break_glass_policy,
                authenticator=human_authenticator,
                evidence_verifier=break_glass_evidence_verifier,
            )
            break_glass_id = break_glass.authorization_id

        current_observation_digest = workspace_observation_sha256(observation)
        try:
            revalidate_authority_for_side_effect(
                w04_authority_decision,
                w04_authority_request,
                ledger=w04_authority_ledger,
                current_context=normalized_context,
                workspace_observation_sha256=str(current_observation_digest),
                adapter_id="managed-mutation-runtime",
                service_identity=str(service_identity),
                evaluated_at=evaluated_at,
                purpose=VerificationPurpose.MUTATION,
            )
        except AuthorityContractError as error:
            _reject(
                "mutation.authority.w04_revalidation",
                f"W04 authority revalidation failed: {error.reason_code}",
            )
        if idempotency_ledger is None:
            _reject(
                "mutation.idempotency.ledger_missing",
                "managed mutation requires a trusted atomic idempotency ledger",
            )
        reservation = idempotency_ledger.reserve_current(
            plan,
            observation,
            workspace_observation_sha256=current_observation_digest,
            service_identity=service_identity,
            evaluated_at=evaluated_at,
        )
        if not isinstance(reservation, IdempotencyReservation):
            _reject(
                "mutation.idempotency.reservation_missing",
                "idempotency key could not be atomically reserved",
            )
        if reservation.idempotency_key != plan.idempotency_key:
            _reject(
                "mutation.idempotency.record_key_mismatch",
                "idempotency reservation is bound to another key",
            )
        if reservation.plan_sha256 != plan.plan_sha256:
            _reject(
                "mutation.idempotency.conflict",
                "the idempotency key is already bound to another plan digest",
            )
        if reservation.workspace_observation_sha256 != current_observation_digest:
            _reject(
                "mutation.idempotency.observation_mismatch",
                "idempotency reservation is bound to another workspace observation",
            )
        _validate_reference(reservation.reservation_record, "idempotency_reservation")
        if not reservation.newly_reserved or reservation.existing_receipt_id is not None:
            _reject(
                "mutation.idempotency.replay",
                "the exact plan already has a reservation or receipt",
            )

        provisional = MutationExecutionAuthorization(
            authorization_id=OpaqueId("mutation-auth-pending"),
            plan_id=plan.plan_id,
            plan_sha256=plan.plan_sha256,
            workspace_id=plan.workspace_id,
            revision_id=observation.revision_id,
            workspace_revision_sha256=plan.before_workspace_revision_sha256,
            manifest_sha256=observation.manifest_sha256,
            workspace_observation_sha256=current_observation_digest,
            idempotency_key=plan.idempotency_key,
            idempotency_reservation=reservation.reservation_record,
            content_observation_sha256=content_observation_digest,
            content_observations=tuple(content_observations),
            content_verifications=content_verifications,
            service_identity=service_identity,
            evaluated_at=evaluated_at.isoformat(),
            gate_context_sha256=gate_context_sha256(normalized_context),
            authority_decision=authority_decision,
            break_glass_authorization_id=break_glass_id,
            break_glass_authorization_sha256=break_glass_authorization_sha256,
            break_glass_request_sha256=bound_break_glass_request_sha256,
            human_approval_verifications=human_verifications,
            break_glass_evidence_verifications=evidence_verifications,
        )
        authorization = replace(
            provisional,
            authorization_id=mutation_execution_authorization_id(provisional),
        )
        try:
            return validate_mutation_execution_authorization_for_plan(
                authorization,
                plan,
            )
        except MutationPlanError as error:
            _reject(error.reason_code, str(error))
