"""Durable, fixture-only managed mutation executor."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Protocol

from video_factory.approvals import GateContext, gate_context_sha256
from video_factory.authority import (
    ActionAuthorityRequest,
    AuthorityContractError,
    AuthorityDecision,
    VerificationPurpose,
    revalidate_authority_for_side_effect,
)
from video_factory.config.canonical import canonical_json_bytes, canonical_sha256
from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    HashDigest,
    OpaqueId,
    RelativeArtifactPath,
)
from video_factory.mutation import (
    MutationExecutionAuthorization,
    MutationKind,
    MutationPlan,
    MutationPlanError,
    MutationReceipt,
    MutationReceiptStatus,
    OperationOutcome,
    OperationResult,
    PathNodeKind,
    PathObservation,
    mutation_execution_authorization_sha256,
    mutation_receipt_from_mapping,
    mutation_receipt_id,
    mutation_receipt_to_mapping,
    validate_mutation_execution_authorization_for_plan,
    validate_mutation_plan,
    workspace_observation_mapping,
    workspace_observation_sha256,
)
from video_factory.mutation.planner import managed_mutation_authority_envelope_sha256
from video_factory.runtime import (
    ExecutionReceipt,
    JournalState,
    RuntimeActionKind,
    build_execution_intent,
    build_execution_receipt,
)

from .boundary import FixtureRuntimeBoundary
from .authority import (
    ClaimBoundAuthorizationLedger,
    ClaimBoundLedgerAdapter,
    runtime_authority_reservation_sha256,
)
from .filesystem import (
    ContentAddressedFixtureStore,
    FixtureAtomicMutationPort,
    FixtureWorkspaceObserver,
    RuntimeFilesystemError,
    _write_new,
    read_stable_regular_file,
    read_stable_regular_bytes,
)
from .journal import (
    AuthorityReservationClaim,
    ExecutionJournalError,
    JournalSnapshot,
    SQLiteExecutionJournal,
    journal_event_reference,
)


MUTATION_RECEIPT_VERSION = "mutation-receipt/1.0"
RECOVERY_PLAN_VERSION = "runtime-reconciliation-plan/1.0"
MUTATION_AUTHORIZATION_VERIFICATION_VERSION = (
    "mutation-authorization-verification/1.0"
)


class ManagedMutationRuntimeError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class RuntimeAuthorityReservation:
    receipt_sha256: HashDigest
    reservation_sha256: HashDigest


@dataclass(frozen=True, slots=True)
class MutationAuthorizationVerification:
    """Trusted proof that W02 issued the exact authorization being consumed."""

    plan_sha256: HashDigest
    authorization_sha256: HashDigest
    service_identity: OpaqueId
    verification_record: ArtifactReference
    verified_at: datetime


class TrustedRuntimeClock(Protocol):
    def now(self) -> datetime: ...


class RuntimeIdentityAttestor(Protocol):
    def attest_current(
        self, service_identity: OpaqueId, *, evaluated_at: datetime
    ) -> ArtifactReference | None: ...


class RuntimeKillSwitch(Protocol):
    def engaged(self, *, evaluated_at: datetime) -> bool: ...


class FreshMutationAuthorityPort(Protocol):
    def revalidate(
        self,
        plan: MutationPlan,
        authorization: MutationExecutionAuthorization,
        *,
        workspace_observation_sha256: HashDigest,
        service_identity: OpaqueId,
        evaluated_at: datetime,
        purpose: VerificationPurpose,
        runtime_claim_sha256: HashDigest,
    ) -> RuntimeAuthorityReservation | None: ...


class TrustedMutationAuthorizationVerifier(Protocol):
    def verify_current(
        self,
        plan: MutationPlan,
        authorization: MutationExecutionAuthorization,
        *,
        service_identity: OpaqueId,
        evaluated_at: datetime,
    ) -> MutationAuthorizationVerification | None: ...


class AuthorityReservationSettlementPort(Protocol):
    def finalize_current(
        self,
        *,
        journal_id: OpaqueId,
        reservation_claim_sha256s: tuple[HashDigest, ...],
        reservation_sha256s: tuple[HashDigest, ...],
        final_state: JournalState,
        evaluated_at: datetime,
    ) -> ArtifactReference | None: ...


class MutationFaultHook(Protocol):
    def after_dispatching(
        self,
        plan: MutationPlan,
        operation_id: OpaqueId,
        target: Path,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class W04MutationAuthorityAdapter:
    request: ActionAuthorityRequest
    decision: AuthorityDecision
    ledger: ClaimBoundAuthorizationLedger
    current_context: GateContext

    def revalidate(
        self,
        plan: MutationPlan,
        authorization: MutationExecutionAuthorization,
        *,
        workspace_observation_sha256: HashDigest,
        service_identity: OpaqueId,
        evaluated_at: datetime,
        purpose: VerificationPurpose,
        runtime_claim_sha256: HashDigest,
    ) -> RuntimeAuthorityReservation | None:
        authority_reference = authorization.authority_decision
        if (
            authority_reference is None
            or str(authority_reference.artifact_version) != "authority-decision/1.0"
            or authority_reference.sha256 != self.decision.decision_sha256
            or self.request.request_id != plan.request_id
            or self.request.request_envelope_sha256
            != managed_mutation_authority_envelope_sha256(plan)
            or self.request.idempotency_key != plan.idempotency_key
            or self.request.executable_plan_sha256 != plan.plan_sha256
            or self.request.scope.workspace_id != plan.workspace_id
            or self.request.requester_principal_id != plan.requester_id
            or self.request.gate_context != self.current_context
            or gate_context_sha256(self.current_context)
            != authorization.gate_context_sha256
        ):
            raise ManagedMutationRuntimeError(
                "runtime.mutation.authority_binding",
                "W04 authority is bound to another exact mutation",
            )
        try:
            verified = revalidate_authority_for_side_effect(
                self.decision,
                self.request,
                ledger=ClaimBoundLedgerAdapter(
                    self.ledger, runtime_claim_sha256
                ),
                current_context=self.current_context,
                workspace_observation_sha256=str(workspace_observation_sha256),
                adapter_id="managed-mutation-runtime",
                service_identity=str(service_identity),
                evaluated_at=evaluated_at,
                purpose=purpose,
            )
        except AuthorityContractError as error:
            raise ManagedMutationRuntimeError(
                "runtime.mutation.authority_denied",
                f"fresh W04 authority failed: {error.reason_code}",
            ) from error
        receipt_sha = verified.receipt.receipt_sha256
        stable_reservation_sha = runtime_authority_reservation_sha256(
            verified,
            runtime_claim_sha256=runtime_claim_sha256,
            expected_purpose=purpose,
        )
        reservation_sha = canonical_sha256(
            {
                "artifact_version": "runtime-mutation-authority-reservation/1.1",
                "w04_reservation_sha256": str(stable_reservation_sha),
                "w02_reservation": {
                    "path": str(authorization.idempotency_reservation.path),
                    "sha256": str(authorization.idempotency_reservation.sha256),
                    "artifact_version": str(
                        authorization.idempotency_reservation.artifact_version
                    ),
                },
                "purpose": purpose.value,
            }
        )
        return RuntimeAuthorityReservation(receipt_sha, reservation_sha)


class FixtureManagedMutationExecutor:
    """Apply one exact plan inside an isolated marker-bound fixture."""

    def __init__(
        self,
        boundary: FixtureRuntimeBoundary,
        *,
        journal: SQLiteExecutionJournal,
        observer: FixtureWorkspaceObserver,
        content_store: ContentAddressedFixtureStore,
        authorization_verifier: TrustedMutationAuthorizationVerifier,
        authority: FreshMutationAuthorityPort,
        identity_attestor: RuntimeIdentityAttestor,
        kill_switch: RuntimeKillSwitch,
        settlement: AuthorityReservationSettlementPort,
        clock: TrustedRuntimeClock,
        service_identity: OpaqueId,
        fault_hook: MutationFaultHook | None = None,
    ) -> None:
        self._boundary = boundary
        self._journal = journal
        self._observer = observer
        self._content = content_store
        self._authorization_verifier = authorization_verifier
        self._atomic_mutation = FixtureAtomicMutationPort(
            boundary,
            observer=observer,
            content_store=content_store,
        )
        self._authority = authority
        self._identity = identity_attestor
        self._kill_switch = kill_switch
        self._settlement = settlement
        self._clock = clock
        self._service_identity = service_identity
        self._fault_hook = fault_hook
        self._receipt_directory = boundary.require_directory("receipts", create=True)
        self._recovery_directory = boundary.require_directory("recovery", create=True)
        self._observation_directory = boundary.require_directory(
            "observations", create=True
        )

    def _require_authorization_current(
        self,
        plan: MutationPlan,
        authorization: MutationExecutionAuthorization,
        *,
        evaluated_at: datetime,
    ) -> MutationAuthorizationVerification:
        expected_sha256 = mutation_execution_authorization_sha256(authorization)
        verification = self._authorization_verifier.verify_current(
            plan,
            authorization,
            service_identity=self._service_identity,
            evaluated_at=evaluated_at,
        )
        if (
            not isinstance(verification, MutationAuthorizationVerification)
            or verification.plan_sha256 != plan.plan_sha256
            or verification.authorization_sha256 != expected_sha256
            or verification.service_identity != self._service_identity
            or verification.verified_at != evaluated_at
            or str(verification.verification_record.artifact_version)
            != MUTATION_AUTHORIZATION_VERIFICATION_VERSION
        ):
            raise ManagedMutationRuntimeError(
                "runtime.mutation.authorization_unverified",
                "trusted W02 issuance verification is missing or rebound",
            )
        return verification

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ManagedMutationRuntimeError(
                "runtime.mutation.clock", "trusted runtime clock returned a naive time"
            )
        return value

    @staticmethod
    def _artifact_mapping(reference: ArtifactReference) -> dict[str, str]:
        return {
            "path": str(reference.path),
            "sha256": str(reference.sha256),
            "artifact_version": str(reference.artifact_version),
        }

    def _persist_mapping(
        self,
        directory: Path,
        *,
        file_name: str,
        logical_path: str,
        artifact_version: str,
        mapping: dict[str, object],
    ) -> ArtifactReference:
        payload = canonical_json_bytes(mapping)
        digest = HashDigest(hashlib.sha256(payload).hexdigest())
        path = directory / file_name
        if path.exists():
            stable = read_stable_regular_file(path)
            if stable.exact_sha256 != digest or stable.byte_length != len(payload):
                raise ManagedMutationRuntimeError(
                    "runtime.mutation.artifact_conflict",
                    f"durable runtime artifact differs: {file_name}",
                )
        else:
            _write_new(path, payload)
        return ArtifactReference(
            path=logical_path,
            sha256=digest,
            artifact_version=ArtifactVersion(artifact_version),
        )

    def _load_mutation_receipt(self, snapshot: JournalSnapshot) -> MutationReceipt:
        if snapshot.receipt is None or len(snapshot.receipt.output_refs) != 1:
            raise ManagedMutationRuntimeError(
                "runtime.mutation.receipt_missing", "journal has no exact mutation receipt"
            )
        reference = snapshot.receipt.output_refs[0]
        if str(reference.artifact_version) != MUTATION_RECEIPT_VERSION:
            raise ManagedMutationRuntimeError(
                "runtime.mutation.receipt_version", "journal output is not a mutation receipt"
            )
        path = self._receipt_directory / Path(str(reference.path)).name
        stable, payload = read_stable_regular_bytes(path)
        try:
            document = json.loads(payload)
            receipt = mutation_receipt_from_mapping(document)
        except Exception as error:
            raise ManagedMutationRuntimeError(
                "runtime.mutation.receipt_corrupt", "durable mutation receipt is invalid"
            ) from error
        if stable.exact_sha256 != reference.sha256 or stable.exact_sha256 != HashDigest(
            hashlib.sha256(canonical_json_bytes(document)).hexdigest()
        ):
            raise ManagedMutationRuntimeError(
                "runtime.mutation.receipt_rebound", "mutation receipt bytes changed"
            )
        return receipt

    def _require_journal_binding(
        self,
        snapshot: JournalSnapshot,
        plan: MutationPlan,
        authorization: MutationExecutionAuthorization,
    ) -> None:
        authority_decision = authorization.authority_decision
        checks = (
            snapshot.intent.action_kind is RuntimeActionKind.MUTATION,
            str(snapshot.intent.action_id) == "managed_mutation",
            snapshot.intent.request_id == plan.request_id,
            snapshot.intent.request_sha256 == plan.change_request_sha256,
            snapshot.intent.idempotency_key == plan.idempotency_key,
            snapshot.intent.workspace_id == plan.workspace_id,
            snapshot.intent.plan_sha256 == plan.plan_sha256,
            snapshot.intent.gate_context_sha256
            == authorization.gate_context_sha256,
            snapshot.intent.workspace_observation_sha256
            == authorization.workspace_observation_sha256,
            authority_decision is not None,
            snapshot.intent.authority_decision_sha256
            == (authority_decision.sha256 if authority_decision is not None else None),
            snapshot.intent.authority_receipt_sha256
            == mutation_execution_authorization_sha256(authorization),
            snapshot.intent.service_identity == self._service_identity,
            authorization.service_identity == self._service_identity,
            snapshot.intent.credential_handle_id is None,
            snapshot.intent.destination is None,
        )
        if not all(checks):
            raise ManagedMutationRuntimeError(
                "runtime.mutation.journal_rebound",
                "mutation inputs differ from the durable execution intent",
            )

    def _recovery_reference(
        self,
        snapshot: JournalSnapshot,
        *,
        reason_code: str,
        observed_sha256: HashDigest,
        created_at: str,
    ) -> ArtifactReference:
        document = {
            "artifact_version": RECOVERY_PLAN_VERSION,
            "journal_id": str(snapshot.journal_id),
            "intent_sha256": str(snapshot.intent.intent_sha256),
            "observed_workspace_sha256": str(observed_sha256),
            "reason_code": reason_code,
            "created_at": created_at,
            "redispatch_allowed": False,
            "reconciliation_required": True,
        }
        return self._persist_mapping(
            self._recovery_directory,
            file_name=f"{snapshot.journal_id}.json",
            logical_path=f"runtime-recovery/{snapshot.journal_id}.json",
            artifact_version=RECOVERY_PLAN_VERSION,
            mapping=document,
        )

    def _persist_observation(self, observation) -> ArtifactReference:
        mapping = workspace_observation_mapping(observation)
        digest = workspace_observation_sha256(observation)
        reference = self._persist_mapping(
            self._observation_directory,
            file_name=f"{digest}.json",
            logical_path=f"runtime-observations/{digest}.json",
            artifact_version="workspace-observation/1.0",
            mapping=mapping,
        )
        if reference.sha256 != digest:
            raise ManagedMutationRuntimeError(
                "runtime.mutation.observation_identity",
                "persisted observation digest differs from current workspace evidence",
            )
        return reference

    def _load_observation_entries(
        self, observation_sha256: HashDigest
    ) -> tuple[PathObservation, ...]:
        path = self._observation_directory / f"{observation_sha256}.json"
        stable, payload = read_stable_regular_bytes(path)
        if stable.exact_sha256 != observation_sha256:
            raise ManagedMutationRuntimeError(
                "runtime.mutation.observation_rebound",
                "persisted pre-effect observation was changed",
            )
        try:
            mapping = json.loads(payload)
            if payload != canonical_json_bytes(mapping):
                raise ValueError("noncanonical observation")
            entries = tuple(
                PathObservation(
                    RelativeArtifactPath(str(item["path"])),
                    PathNodeKind(str(item["node_kind"])),
                    (
                        HashDigest(str(item["exact_sha256"]))
                        if item["exact_sha256"] is not None
                        else None
                    ),
                    (
                        int(item["byte_length"])
                        if item["byte_length"] is not None
                        else None
                    ),
                )
                for item in mapping["entries"]
            )
        except Exception as error:
            raise ManagedMutationRuntimeError(
                "runtime.mutation.observation_corrupt",
                "persisted pre-effect observation is invalid",
            ) from error
        return entries

    @staticmethod
    def _expected_entries(
        before: tuple[PathObservation, ...],
        operation,
        *,
        moved_length: int | None,
    ) -> tuple[PathObservation, ...]:
        by_path = {str(item.path): item for item in before}
        path = str(operation.path)
        if operation.kind in {MutationKind.CREATE, MutationKind.REPLACE}:
            assert operation.new_content is not None
            by_path[path] = PathObservation(
                operation.path,
                PathNodeKind.FILE,
                operation.new_content.exact_sha256,
                operation.new_content.byte_length,
            )
        elif operation.kind is MutationKind.DELETE:
            by_path.pop(path, None)
        else:
            assert operation.destination_path is not None
            by_path.pop(path, None)
            by_path[str(operation.destination_path)] = PathObservation(
                operation.destination_path,
                PathNodeKind.FILE,
                operation.expected_before.exact_sha256,
                moved_length,
            )
        return tuple(by_path[key] for key in sorted(by_path))

    def _validate_content(self, operation, *, evaluated_at: datetime) -> None:
        if operation.new_content is None:
            return
        current = self._content.resolve_current(
            operation.new_content, evaluated_at=evaluated_at
        )
        if current is None:
            raise ManagedMutationRuntimeError(
                "runtime.mutation.content_stale", "planned content is no longer current"
            )

    def _validate_operation(self, operation, *, evaluated_at: datetime) -> int | None:
        self._validate_content(operation, evaluated_at=evaluated_at)
        if operation.kind is MutationKind.CREATE:
            self._observer.require_absent(str(operation.path))
            return None
        assert operation.expected_before.exact_sha256 is not None
        stable = self._observer.require_exact_file(
            str(operation.path),
            expected_sha256=operation.expected_before.exact_sha256,
        )
        if operation.kind is MutationKind.MOVE:
            assert operation.destination_path is not None
            self._observer.require_absent(str(operation.destination_path))
        return stable.byte_length

    def _apply_operation(self, operation) -> int | None:
        self._boundary.assert_current()
        return self._atomic_mutation.apply_exact(operation)

    @staticmethod
    def _result_applied(operation, *, moved_length: int | None) -> OperationResult:
        if operation.new_content is not None:
            after_sha = operation.new_content.exact_sha256
            after_length = operation.new_content.byte_length
        elif operation.kind is MutationKind.MOVE:
            after_sha = operation.expected_before.exact_sha256
            after_length = moved_length
        else:
            after_sha = None
            after_length = None
        return OperationResult(
            operation.operation_id,
            operation.kind,
            operation.path,
            operation.destination_path,
            OperationOutcome.APPLIED,
            operation.expected_before.exact_sha256,
            after_sha,
            after_length,
            "mutation.operation.applied",
        )

    @staticmethod
    def _result_not_applied(operation, *, rejected: bool, reason_code: str) -> OperationResult:
        return OperationResult(
            operation.operation_id,
            operation.kind,
            operation.path,
            operation.destination_path,
            OperationOutcome.REJECTED if rejected else OperationOutcome.FAILED,
            operation.expected_before.exact_sha256,
            None,
            None,
            reason_code,
        )

    def _build_domain_receipt(
        self,
        plan: MutationPlan,
        authorization: MutationExecutionAuthorization,
        snapshot: JournalSnapshot,
        *,
        started_at: str,
        completed_at: str,
        status: MutationReceiptStatus,
        before_observation_sha256: HashDigest,
        after_observation_sha256: HashDigest,
        after_manifest_sha256: HashDigest,
        results: tuple[OperationResult, ...],
        journal_record: ArtifactReference,
        recovery: ArtifactReference | None,
        reconciliation_complete: bool = False,
    ) -> tuple[MutationReceipt, ArtifactReference]:
        provisional = MutationReceipt(
            receipt_id=OpaqueId("pending"),
            plan_id=plan.plan_id,
            plan_sha256=plan.plan_sha256,
            workspace_id=plan.workspace_id,
            idempotency_key=plan.idempotency_key,
            executor_identity=self._service_identity,
            execution_authorization_id=authorization.authorization_id,
            execution_authorization_sha256=mutation_execution_authorization_sha256(
                authorization
            ),
            before_workspace_observation_sha256=before_observation_sha256,
            after_workspace_observation_sha256=after_observation_sha256,
            idempotency_reservation=authorization.idempotency_reservation,
            journal_record=journal_record,
            started_at=started_at,
            completed_at=completed_at,
            status=status,
            before_manifest_sha256=plan.before_manifest_sha256,
            after_manifest_sha256=after_manifest_sha256,
            operation_results=results,
            rollback_or_reconciliation_required=(
                recovery is not None and not reconciliation_complete
            ),
            rollback_or_reconciliation_plan=recovery,
        )
        receipt = replace(provisional, receipt_id=mutation_receipt_id(provisional))
        mapping = mutation_receipt_to_mapping(receipt)
        reference = self._persist_mapping(
            self._receipt_directory,
            file_name=(
                f"{snapshot.journal_id}-reconciled.json"
                if reconciliation_complete
                else f"{snapshot.journal_id}.json"
            ),
            logical_path=(
                f"runtime-receipts/{snapshot.journal_id}-reconciled.json"
                if reconciliation_complete
                else f"runtime-receipts/{snapshot.journal_id}.json"
            ),
            artifact_version=MUTATION_RECEIPT_VERSION,
            mapping=mapping,
        )
        return receipt, reference

    def _finish_with_domain_receipt(
        self,
        plan: MutationPlan,
        authorization: MutationExecutionAuthorization,
        snapshot: JournalSnapshot,
        *,
        final_state: JournalState,
        status: MutationReceiptStatus,
        started_at: str,
        completed_at: str,
        before_sha: HashDigest,
        after_sha: HashDigest,
        after_manifest_sha256: HashDigest,
        results: tuple[OperationResult, ...],
        authority_sha: HashDigest | None,
        reservation_sha: HashDigest | None,
        settlement: ArtifactReference | None,
        recovery: ArtifactReference | None,
        reason_codes: tuple[str, ...],
        reconciliation_record: ArtifactReference | None = None,
        reconciliation_complete: bool = False,
    ) -> MutationReceipt:
        domain: list[MutationReceipt] = []

        def receipt_factory(event) -> ExecutionReceipt:
            mutation_receipt, mutation_reference = self._build_domain_receipt(
                plan,
                authorization,
                snapshot,
                started_at=started_at,
                completed_at=completed_at,
                status=status,
                before_observation_sha256=before_sha,
                after_observation_sha256=after_sha,
                after_manifest_sha256=after_manifest_sha256,
                results=results,
                journal_record=journal_event_reference(event),
                recovery=recovery,
                reconciliation_complete=reconciliation_complete,
            )
            domain.append(mutation_receipt)
            return build_execution_receipt(
                intent_sha256=snapshot.intent.intent_sha256,
                journal_id=snapshot.journal_id,
                journal_head_sha256=event.event_sha256,
                final_state=final_state,
                started_at=started_at,
                completed_at=completed_at,
                output_refs=(mutation_reference,),
                measured_cost_minor_units=None,
                currency=None,
                before_workspace_observation_sha256=before_sha,
                after_workspace_observation_sha256=after_sha,
                authority_receipt_sha256=authority_sha,
                settlement_record=settlement,
                reconciliation_record=reconciliation_record,
                reason_codes=tuple(sorted(set(reason_codes))),
            )

        event_observation = after_sha if authority_sha is not None else None
        self._journal.finish(
            snapshot.journal_id,
            final_state,
            occurred_at=completed_at,
            reason_codes=tuple(sorted(set(reason_codes))),
            authority_receipt_sha256=authority_sha,
            workspace_observation_sha256=event_observation,
            reservation_sha256=reservation_sha,
            receipt_factory=receipt_factory,
        )
        if len(domain) != 1:
            raise ManagedMutationRuntimeError(
                "runtime.mutation.receipt_missing",
                "atomic journal finish did not create a mutation receipt",
            )
        return domain[0]

    def _classify_operation_application(self, operation) -> tuple[bool | None, int | None]:
        def exists_no_follow(path: Path) -> bool:
            try:
                path.lstat()
            except FileNotFoundError:
                return False
            except OSError:
                return True
            return True

        def exact(path_value: str, digest: HashDigest) -> tuple[bool | None, int | None]:
            path = self._observer.managed_path(path_value)
            try:
                stable = read_stable_regular_file(path)
            except RuntimeFilesystemError as error:
                if error.reason_code == "runtime.filesystem.file_missing":
                    return False, None
                return None, None
            return stable.exact_sha256 == digest, stable.byte_length

        path = str(operation.path)
        if operation.kind in {MutationKind.CREATE, MutationKind.REPLACE}:
            assert operation.new_content is not None
            matches_new, new_length = exact(path, operation.new_content.exact_sha256)
            if matches_new:
                return True, new_length
            if operation.kind is MutationKind.CREATE:
                candidate = self._observer.managed_path(path)
                return (False, None) if not exists_no_follow(candidate) else (None, None)
            assert operation.expected_before.exact_sha256 is not None
            matches_old, old_length = exact(path, operation.expected_before.exact_sha256)
            return (False, old_length) if matches_old else (None, None)
        if operation.kind is MutationKind.DELETE:
            candidate = self._observer.managed_path(path)
            if not exists_no_follow(candidate):
                return True, None
            assert operation.expected_before.exact_sha256 is not None
            matches_old, old_length = exact(path, operation.expected_before.exact_sha256)
            return (False, old_length) if matches_old else (None, None)
        assert operation.destination_path is not None
        assert operation.expected_before.exact_sha256 is not None
        source_match, source_length = exact(path, operation.expected_before.exact_sha256)
        destination_match, destination_length = exact(
            str(operation.destination_path), operation.expected_before.exact_sha256
        )
        source_exists = exists_no_follow(self._observer.managed_path(path))
        destination_exists = exists_no_follow(
            self._observer.managed_path(str(operation.destination_path))
        )
        if source_match and not destination_exists:
            return False, source_length
        if not source_exists and destination_match:
            return True, destination_length
        return None, None

    def _reconciliation_reference(
        self,
        snapshot: JournalSnapshot,
        *,
        operation_states: tuple[tuple[str, bool], ...],
        observed_sha256: HashDigest,
        completed_at: str,
    ) -> ArtifactReference:
        document = {
            "artifact_version": "runtime-reconciliation-record/1.0",
            "journal_id": str(snapshot.journal_id),
            "intent_sha256": str(snapshot.intent.intent_sha256),
            "operation_states": [
                {"operation_id": operation_id, "applied": applied}
                for operation_id, applied in operation_states
            ],
            "observed_workspace_sha256": str(observed_sha256),
            "completed_at": completed_at,
            "redispatch_performed": False,
            "reconciled": True,
        }
        return self._persist_mapping(
            self._recovery_directory,
            file_name=f"{snapshot.journal_id}-reconciled.json",
            logical_path=f"runtime-recovery/{snapshot.journal_id}-reconciled.json",
            artifact_version="runtime-reconciliation-record/1.0",
            mapping=document,
        )

    def reconcile_pending(
        self,
        plan: MutationPlan,
        authorization: MutationExecutionAuthorization,
    ) -> MutationReceipt:
        """Reconcile may-have-started work from observed bytes; never redispatch it."""

        self._boundary.assert_current()
        try:
            validate_mutation_plan(plan)
            validate_mutation_execution_authorization_for_plan(authorization, plan)
        except MutationPlanError as error:
            raise ManagedMutationRuntimeError(error.reason_code, str(error)) from error
        evaluated_at = self._aware(self._clock.now())
        self._require_authorization_current(
            plan, authorization, evaluated_at=evaluated_at
        )
        snapshot = self._journal.load_by_scope(
            action_kind=RuntimeActionKind.MUTATION,
            action_id=OpaqueId("managed_mutation"),
            idempotency_key=plan.idempotency_key,
            request_sha256=plan.change_request_sha256,
        )
        if snapshot is None or not snapshot.reconcile_only:
            raise ManagedMutationRuntimeError(
                "runtime.mutation.reconcile_state",
                "only may-have-started mutation work can be reconciled",
            )
        self._require_journal_binding(snapshot, plan, authorization)
        attestation = self._identity.attest_current(
            self._service_identity, evaluated_at=evaluated_at
        )
        if attestation is None:
            raise ManagedMutationRuntimeError(
                "runtime.mutation.reconcile_identity",
                "reconciliation requires current service identity and authority",
            )
        if self._kill_switch.engaged(evaluated_at=evaluated_at):
            raise ManagedMutationRuntimeError(
                "runtime.mutation.kill_switch", "runtime kill switch is engaged"
            )
        observed = self._observer.observe(revision_id=plan.before_revision_id)
        observed_sha = workspace_observation_sha256(observed)
        baseline_entries = self._load_observation_entries(
            authorization.workspace_observation_sha256
        )
        claim = self._journal.prepare_reservation_claim(
            snapshot.journal_id,
            purpose=VerificationPurpose.RECONCILE,
            effect_id=OpaqueId(
                "mutation-reconcile-"
                + str(
                    sum(
                        event.state is JournalState.RECONCILING
                        for event in snapshot.events
                    )
                )
            ),
            expected_head_sha256=snapshot.events[-1].event_sha256,
            occurred_at=evaluated_at.isoformat(),
        )
        reservation = self._authority.revalidate(
            plan,
            authorization,
            workspace_observation_sha256=observed_sha,
            service_identity=self._service_identity,
            evaluated_at=evaluated_at,
            purpose=VerificationPurpose.RECONCILE,
            runtime_claim_sha256=claim.claim_sha256,
        )
        if reservation is None:
            raise ManagedMutationRuntimeError(
                "runtime.mutation.reconcile_authority",
                "fresh reconciliation authority was denied",
            )
        self._journal.record_reservation_result(
            claim,
            authority_receipt_sha256=reservation.receipt_sha256,
            reservation_sha256=reservation.reservation_sha256,
            verified_at=evaluated_at.isoformat(),
        )
        dispatched = {
            str(event.operation_id)
            for event in snapshot.events
            if event.state is JournalState.DISPATCHED and event.operation_id is not None
        }
        active: str | None = None
        for event in snapshot.events:
            if event.state is JournalState.DISPATCHING and event.operation_id is not None:
                active = str(event.operation_id)
            elif (
                event.state is JournalState.DISPATCHED
                and event.operation_id is not None
                and active == str(event.operation_id)
            ):
                active = None
        outcomes: list[tuple[object, bool, int | None]] = []
        ambiguous = False
        for operation in plan.operations:
            applied, length = self._classify_operation_application(operation)
            operation_id = str(operation.operation_id)
            expected = True if operation_id in dispatched else None if operation_id == active else False
            if applied is None or (expected is not None and applied is not expected):
                ambiguous = True
                break
            outcomes.append((operation, applied, length))
        expected_entries = baseline_entries
        if not ambiguous:
            for operation, applied, length in outcomes:
                if applied:
                    expected_entries = self._expected_entries(
                        expected_entries, operation, moved_length=length
                    )
            if observed.entries != expected_entries:
                ambiguous = True
        if ambiguous:
            if snapshot.receipt is None and snapshot.state in {
                JournalState.DISPATCHING,
                JournalState.DISPATCHED,
                JournalState.UNCERTAIN,
            }:
                self._journal.append_state(
                    snapshot.journal_id,
                    JournalState.UNCERTAIN,
                    occurred_at=evaluated_at.isoformat(),
                    reason_codes=("runtime.mutation.reconcile_ambiguous",),
                    authority_receipt_sha256=reservation.receipt_sha256,
                    workspace_observation_sha256=observed_sha,
                    reservation_sha256=reservation.reservation_sha256,
                )
            raise ManagedMutationRuntimeError(
                "runtime.mutation.reconcile_ambiguous",
                "current bytes cannot prove whether the operation was applied",
            )
        operation_states = tuple(
            (str(operation.operation_id), applied)
            for operation, applied, _ in outcomes
        )
        reconciliation = self._reconciliation_reference(
            snapshot,
            operation_states=operation_states,
            observed_sha256=observed_sha,
            completed_at=evaluated_at.isoformat(),
        )
        settlement_claims = self._journal.unsettled_reservation_claims(
            snapshot.journal_id
        )
        settlement_reservations = tuple(
            result[1]
            for item in settlement_claims
            for result in (self._journal.reservation_result(item),)
            if result is not None
        )
        if len(settlement_reservations) != len(settlement_claims):
            raise ManagedMutationRuntimeError(
                "runtime.mutation.reconcile_reservation",
                "an unsettled reservation claim lacks its trusted ledger result",
            )
        settlement = self._settlement.finalize_current(
            journal_id=snapshot.journal_id,
            reservation_claim_sha256s=tuple(
                item.claim_sha256 for item in settlement_claims
            ),
            reservation_sha256s=settlement_reservations,
            final_state=JournalState.RECONCILED,
            evaluated_at=evaluated_at,
        )
        if settlement is None:
            raise ManagedMutationRuntimeError(
                "runtime.mutation.reconcile_settlement",
                "reconciled authority reservation was not durably settled",
            )
        self._journal.record_claim_settlement(
            settlement_claims,
            settlement_record=settlement,
            final_state=JournalState.RECONCILED,
        )
        results = tuple(
            self._result_applied(operation, moved_length=length)
            if applied
            else self._result_not_applied(
                operation,
                rejected=True,
                reason_code="mutation.operation.reconciled_not_applied",
            )
            for operation, applied, length in outcomes
        )
        return self._finish_with_domain_receipt(
            plan,
            authorization,
            snapshot,
            final_state=JournalState.RECONCILED,
            status=MutationReceiptStatus.RECONCILED,
            started_at=authorization.evaluated_at,
            completed_at=evaluated_at.isoformat(),
            before_sha=authorization.workspace_observation_sha256,
            after_sha=observed_sha,
            after_manifest_sha256=observed.manifest_sha256,
            results=results,
            authority_sha=reservation.receipt_sha256,
            reservation_sha=reservation.reservation_sha256,
            settlement=settlement,
            recovery=reconciliation,
            reason_codes=("runtime.mutation.reconciled",),
            reconciliation_record=reconciliation,
            reconciliation_complete=True,
        )

    def apply(
        self,
        plan: MutationPlan,
        authorization: MutationExecutionAuthorization,
    ) -> MutationReceipt:
        self._boundary.assert_current()
        try:
            validate_mutation_plan(plan)
            validate_mutation_execution_authorization_for_plan(authorization, plan)
        except MutationPlanError as error:
            raise ManagedMutationRuntimeError(error.reason_code, str(error)) from error
        identity_time = self._aware(self._clock.now())
        self._require_authorization_current(
            plan, authorization, evaluated_at=identity_time
        )
        existing = self._journal.load_by_scope(
            action_kind=RuntimeActionKind.MUTATION,
            action_id=OpaqueId("managed_mutation"),
            idempotency_key=plan.idempotency_key,
            request_sha256=plan.change_request_sha256,
        )
        if existing is not None:
            self._require_journal_binding(existing, plan, authorization)
            if existing.receipt is not None:
                return self._load_mutation_receipt(existing)
            if existing.reconcile_only:
                raise ManagedMutationRuntimeError(
                    "runtime.mutation.reconcile_required",
                    "may-have-started mutation requires reconciliation, never redispatch",
                )
        if authorization.service_identity != self._service_identity:
            raise ManagedMutationRuntimeError(
                "runtime.mutation.service_identity", "authorization names another service"
            )
        created_at = authorization.evaluated_at
        attestation = self._identity.attest_current(
            self._service_identity, evaluated_at=identity_time
        )
        if attestation is None:
            raise ManagedMutationRuntimeError(
                "runtime.mutation.identity_attestation",
                "current service identity could not be attested",
            )
        assert authorization.authority_decision is not None
        intent = build_execution_intent(
            request_id=plan.request_id,
            request_sha256=plan.change_request_sha256,
            idempotency_key=plan.idempotency_key,
            action_kind=RuntimeActionKind.MUTATION,
            action_id=OpaqueId("managed_mutation"),
            planned_effect_ids=tuple(
                operation.operation_id for operation in plan.operations
            ),
            workspace_id=plan.workspace_id,
            plan_sha256=plan.plan_sha256,
            gate_context_sha256=authorization.gate_context_sha256,
            workspace_observation_sha256=authorization.workspace_observation_sha256,
            authority_decision_sha256=authorization.authority_decision.sha256,
            authority_receipt_sha256=mutation_execution_authorization_sha256(
                authorization
            ),
            service_identity=self._service_identity,
            service_identity_attestation=attestation,
            credential_handle_id=None,
            credential_verification=None,
            destination=None,
            created_at=created_at,
        )
        snapshot, created = self._journal.reserve_planned(
            intent,
            occurred_at=created_at,
            planned_effect_ids=tuple(
                operation.operation_id for operation in plan.operations
            ),
        )
        if not created:
            if snapshot.receipt is not None:
                return self._load_mutation_receipt(snapshot)
            if snapshot.reconcile_only:
                raise ManagedMutationRuntimeError(
                    "runtime.mutation.reconcile_required",
                    "may-have-started mutation requires reconciliation, never redispatch",
                )
            if snapshot.state not in {
                JournalState.PLANNED,
                JournalState.AUTHORIZED,
                JournalState.RESERVED,
            }:
                raise ManagedMutationRuntimeError(
                    "runtime.mutation.terminal_receipt_missing",
                    "terminal journal is missing its durable receipt",
                )

        results: list[OperationResult] = []
        claims: list[AuthorityReservationClaim] = []
        reservations: list[HashDigest] = []
        last_authority_sha: HashDigest | None = None
        before_observation_sha = authorization.workspace_observation_sha256
        current_observation = None
        failure_reason = "runtime.mutation.failure"
        try:
            current_observation = self._observer.observe(
                revision_id=plan.before_revision_id
            )
            current_sha = workspace_observation_sha256(current_observation)
            if (
                current_sha != authorization.workspace_observation_sha256
                or current_observation.manifest_sha256 != plan.before_manifest_sha256
                or current_observation.workspace_id != plan.workspace_id
            ):
                raise ManagedMutationRuntimeError(
                    "runtime.mutation.workspace_rebound",
                    "current workspace differs from the authorized exact observation",
                )
            self._persist_observation(current_observation)
            for index, operation in enumerate(plan.operations):
                evaluated_at = self._aware(self._clock.now())
                if self._kill_switch.engaged(evaluated_at=evaluated_at):
                    raise ManagedMutationRuntimeError(
                        "runtime.mutation.kill_switch", "runtime kill switch is engaged"
                    )
                if self._identity.attest_current(
                    self._service_identity, evaluated_at=evaluated_at
                ) != attestation:
                    raise ManagedMutationRuntimeError(
                        "runtime.mutation.identity_rebound",
                        "service identity attestation changed before the effect",
                    )
                refreshed = self._observer.observe(revision_id=plan.before_revision_id)
                refreshed_sha = workspace_observation_sha256(refreshed)
                if refreshed_sha != workspace_observation_sha256(current_observation):
                    raise ManagedMutationRuntimeError(
                        "runtime.mutation.concurrent_change",
                        "workspace changed outside the managed operation sequence",
                    )
                moved_length = self._validate_operation(
                    operation, evaluated_at=evaluated_at
                )
                claim = self._journal.prepare_reservation_claim(
                    snapshot.journal_id,
                    purpose=VerificationPurpose.MUTATION,
                    effect_id=operation.operation_id,
                    expected_head_sha256=self._journal.load(
                        snapshot.journal_id
                    ).events[-1].event_sha256,
                    occurred_at=evaluated_at.isoformat(),
                )
                reservation = self._authority.revalidate(
                    plan,
                    authorization,
                    workspace_observation_sha256=refreshed_sha,
                    service_identity=self._service_identity,
                    evaluated_at=evaluated_at,
                    purpose=VerificationPurpose.MUTATION,
                    runtime_claim_sha256=claim.claim_sha256,
                )
                if reservation is None:
                    raise ManagedMutationRuntimeError(
                        "runtime.mutation.authority_missing",
                        "fresh mutation authority reservation was denied",
                    )
                self._journal.record_reservation_result(
                    claim,
                    authority_receipt_sha256=reservation.receipt_sha256,
                    reservation_sha256=reservation.reservation_sha256,
                    verified_at=evaluated_at.isoformat(),
                )
                last_authority_sha = reservation.receipt_sha256
                claims.append(claim)
                reservations.append(reservation.reservation_sha256)
                current_state = self._journal.load(snapshot.journal_id).state
                if index == 0 and current_state in {
                    JournalState.PLANNED,
                    JournalState.AUTHORIZED,
                }:
                    self._journal.append_state(
                        snapshot.journal_id,
                        JournalState.AUTHORIZED,
                        occurred_at=evaluated_at.isoformat(),
                        authority_receipt_sha256=last_authority_sha,
                        workspace_observation_sha256=refreshed_sha,
                    )
                    current_state = JournalState.AUTHORIZED
                    self._journal.append_state(
                        snapshot.journal_id,
                        JournalState.RESERVED,
                        occurred_at=evaluated_at.isoformat(),
                        reservation_sha256=reservation.reservation_sha256,
                    )
                elif index == 0 and current_state is JournalState.RESERVED:
                    self._journal.append_state(
                        snapshot.journal_id,
                        JournalState.RESERVED,
                        occurred_at=evaluated_at.isoformat(),
                        authority_receipt_sha256=last_authority_sha,
                        workspace_observation_sha256=refreshed_sha,
                        reservation_sha256=reservation.reservation_sha256,
                    )
                dispatch_head = self._journal.load(
                    snapshot.journal_id
                ).events[-1].event_sha256
                self._journal.claim_dispatch(
                    snapshot.journal_id,
                    expected_head_sha256=dispatch_head,
                    occurred_at=evaluated_at.isoformat(),
                    operation_id=operation.operation_id,
                    authority_receipt_sha256=last_authority_sha,
                    workspace_observation_sha256=refreshed_sha,
                    reservation_sha256=reservation.reservation_sha256,
                )
                target = self._observer.managed_path(str(operation.path))
                if self._fault_hook is not None:
                    self._fault_hook.after_dispatching(
                        plan, operation.operation_id, target
                    )
                effect_at = self._aware(self._clock.now())
                if self._kill_switch.engaged(evaluated_at=effect_at):
                    raise ManagedMutationRuntimeError(
                        "runtime.mutation.kill_switch",
                        "runtime kill switch changed after the dispatch marker",
                    )
                if self._identity.attest_current(
                    self._service_identity, evaluated_at=effect_at
                ) != attestation:
                    raise ManagedMutationRuntimeError(
                        "runtime.mutation.identity_rebound",
                        "service identity changed after the dispatch marker",
                    )
                final_observation = self._observer.observe(
                    revision_id=plan.before_revision_id
                )
                final_observation_sha = workspace_observation_sha256(
                    final_observation
                )
                if final_observation_sha != refreshed_sha:
                    raise ManagedMutationRuntimeError(
                        "runtime.mutation.concurrent_change",
                        "full workspace manifest changed after the dispatch marker",
                    )
                moved_length = self._validate_operation(
                    operation, evaluated_at=effect_at
                )
                effect_reservation = self._authority.revalidate(
                    plan,
                    authorization,
                    workspace_observation_sha256=final_observation_sha,
                    service_identity=self._service_identity,
                    evaluated_at=effect_at,
                    purpose=VerificationPurpose.MUTATION,
                    runtime_claim_sha256=claim.claim_sha256,
                )
                if (
                    effect_reservation is None
                    or effect_reservation.reservation_sha256
                    != reservation.reservation_sha256
                ):
                    raise ManagedMutationRuntimeError(
                        "runtime.mutation.authority_rebound",
                        "fresh effect-time authority differs from the durable claim",
                    )
                self._journal.record_reservation_result(
                    claim,
                    authority_receipt_sha256=effect_reservation.receipt_sha256,
                    reservation_sha256=effect_reservation.reservation_sha256,
                    verified_at=effect_at.isoformat(),
                )
                last_authority_sha = effect_reservation.receipt_sha256
                self._require_authorization_current(
                    plan, authorization, evaluated_at=effect_at
                )
                expected_entries = self._expected_entries(
                    final_observation.entries,
                    operation,
                    moved_length=moved_length,
                )
                applied_length = self._apply_operation(operation)
                if applied_length != moved_length:
                    raise ManagedMutationRuntimeError(
                        "runtime.mutation.atomic_rebound",
                        "atomic mutation staged different source bytes",
                    )
                after_effect = (
                    getattr(self._fault_hook, "after_effect", None)
                    if self._fault_hook is not None
                    else None
                )
                if callable(after_effect):
                    after_effect(plan, operation.operation_id, target)
                after = self._observer.observe(revision_id=plan.before_revision_id)
                if after.entries != expected_entries:
                    raise ManagedMutationRuntimeError(
                        "runtime.mutation.postcondition",
                        "workspace bytes do not match the exact operation result",
                    )
                after_sha = workspace_observation_sha256(after)
                self._journal.append_state(
                    snapshot.journal_id,
                    JournalState.DISPATCHED,
                    occurred_at=self._aware(self._clock.now()).isoformat(),
                    operation_id=operation.operation_id,
                    authority_receipt_sha256=last_authority_sha,
                    workspace_observation_sha256=after_sha,
                    reservation_sha256=reservation.reservation_sha256,
                )
                results.append(
                    self._result_applied(operation, moved_length=moved_length)
                )
                current_observation = after

            completed = self._aware(self._clock.now())
            settlement = self._settlement.finalize_current(
                journal_id=snapshot.journal_id,
                reservation_claim_sha256s=tuple(
                    claim.claim_sha256 for claim in claims
                ),
                reservation_sha256s=tuple(reservations),
                final_state=JournalState.SUCCEEDED,
                evaluated_at=completed,
            )
            if settlement is None:
                raise ManagedMutationRuntimeError(
                    "runtime.mutation.settlement",
                    "authority reservation settlement was not durably recorded",
                )
            self._journal.record_claim_settlement(
                tuple(claims),
                settlement_record=settlement,
                final_state=JournalState.SUCCEEDED,
            )
            assert current_observation is not None
            final_sha = workspace_observation_sha256(current_observation)
            return self._finish_with_domain_receipt(
                plan,
                authorization,
                snapshot,
                final_state=JournalState.SUCCEEDED,
                status=MutationReceiptStatus.SUCCEEDED,
                started_at=created_at,
                completed_at=completed.isoformat(),
                before_sha=before_observation_sha,
                after_sha=final_sha,
                after_manifest_sha256=current_observation.manifest_sha256,
                results=tuple(results),
                authority_sha=last_authority_sha,
                reservation_sha=(reservations[-1] if reservations else None),
                settlement=settlement,
                recovery=None,
                reason_codes=(),
            )
        except Exception as error:
            if isinstance(error, ManagedMutationRuntimeError):
                failure_reason = error.reason_code
            elif isinstance(error, (RuntimeFilesystemError, ExecutionJournalError)):
                failure_reason = error.reason_code
            else:
                failure_reason = "runtime.mutation.effect_uncertain"
            current = self._journal.load(snapshot.journal_id)
            may_have_started = current.state is JournalState.DISPATCHING
            completed = self._aware(self._clock.now())
            try:
                observed = self._observer.observe(revision_id=plan.before_revision_id)
                after_sha = workspace_observation_sha256(observed)
                after_manifest = observed.manifest_sha256
            except Exception:
                after_sha = canonical_sha256(
                    {
                        "artifact_version": "unobserved-workspace/1.0",
                        "journal_id": str(snapshot.journal_id),
                        "reason_code": failure_reason,
                    }
                )
                after_manifest = after_sha
            if may_have_started or len(results) == len(plan.operations):
                final_state = JournalState.UNCERTAIN
                status = MutationReceiptStatus.UNCERTAIN
            elif results:
                final_state = JournalState.PARTIAL
                status = MutationReceiptStatus.PARTIALLY_APPLIED
            elif (
                after_sha == before_observation_sha
                and after_manifest == plan.before_manifest_sha256
            ):
                final_state = JournalState.FAILED
                status = MutationReceiptStatus.REJECTED
            else:
                final_state = JournalState.FAILED
                status = MutationReceiptStatus.FAILED
            completed_ids = {item.operation_id for item in results}
            settlement = (
                self._settlement.finalize_current(
                    journal_id=snapshot.journal_id,
                    reservation_claim_sha256s=tuple(
                        claim.claim_sha256 for claim in claims
                    ),
                    reservation_sha256s=tuple(reservations),
                    final_state=final_state,
                    evaluated_at=completed,
                )
                if reservations
                else None
            )
            if settlement is not None:
                self._journal.record_claim_settlement(
                    tuple(claims),
                    settlement_record=settlement,
                    final_state=final_state,
                )
            if reservations and settlement is None:
                failure_reason = "runtime.mutation.settlement"
                if current.reconcile_only:
                    final_state = JournalState.UNCERTAIN
                    status = MutationReceiptStatus.UNCERTAIN
                else:
                    final_state = JournalState.FAILED
                    status = MutationReceiptStatus.FAILED
            rejected = status is MutationReceiptStatus.REJECTED
            all_results = tuple(results) + tuple(
                self._result_not_applied(
                    operation, rejected=rejected, reason_code=failure_reason
                )
                for operation in plan.operations
                if operation.operation_id not in completed_ids
            )
            recovery = (
                None
                if rejected
                else self._recovery_reference(
                    snapshot,
                    reason_code=failure_reason,
                    observed_sha256=after_sha,
                    created_at=completed.isoformat(),
                )
            )
            return self._finish_with_domain_receipt(
                plan,
                authorization,
                snapshot,
                final_state=final_state,
                status=status,
                started_at=created_at,
                completed_at=completed.isoformat(),
                before_sha=before_observation_sha,
                after_sha=after_sha,
                after_manifest_sha256=after_manifest,
                results=all_results,
                authority_sha=last_authority_sha,
                reservation_sha=(reservations[-1] if reservations else None),
                settlement=settlement,
                recovery=recovery,
                reason_codes=(failure_reason,),
            )


__all__ = [
    "AuthorityReservationSettlementPort",
    "FixtureManagedMutationExecutor",
    "FreshMutationAuthorityPort",
    "MUTATION_AUTHORIZATION_VERIFICATION_VERSION",
    "ManagedMutationRuntimeError",
    "MutationAuthorizationVerification",
    "MutationFaultHook",
    "RuntimeAuthorityReservation",
    "RuntimeIdentityAttestor",
    "RuntimeKillSwitch",
    "TrustedRuntimeClock",
    "TrustedMutationAuthorizationVerifier",
    "W04MutationAuthorityAdapter",
]
