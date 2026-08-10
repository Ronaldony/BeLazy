"""Durable coordinator for fixture-only external executor calls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
from typing import Protocol

from video_factory.approvals import GateContext, gate_context_sha256
from video_factory.authority import (
    ActionAuthorityRequest,
    AuthorityDecision,
    VerificationPurpose,
)
from video_factory.config.canonical import canonical_json_bytes, canonical_sha256
from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    HashDigest,
    OpaqueId,
    RelativeArtifactPath,
    RequestId,
)
from video_factory.mutation import (
    WorkspaceObservation,
    WorkspaceRevision,
    path_collision_key,
    require_managed_path,
    workspace_observation_sha256,
)
from video_factory.providers import (
    AdapterKind,
    CapabilityDescriptor,
    CostMeasurement,
    ExecutorAuthorityScope,
    ExternalReference,
    ExternalStateUncertain,
    OrchestrationAuthorization,
    Outcome,
    RequestEnvelope,
    ResultEnvelope,
    UncertaintyEvidence,
    verify_adapter_dispatch_authority,
    request_envelope_sha256,
    validate_request_envelope,
)
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
from .credentials import (
    CredentialLease,
    CredentialScope,
    TrustedCredentialBroker,
    require_current_credential_lease,
)
from .filesystem import (
    FixtureWorkspaceObserver,
    RuntimeFilesystemError,
    _write_new,
    read_stable_regular_bytes,
    read_stable_regular_file,
)
from .journal import ExecutionJournalError, JournalSnapshot, SQLiteExecutionJournal
from .managed_mutation import (
    AuthorityReservationSettlementPort,
    RuntimeIdentityAttestor,
    RuntimeKillSwitch,
    TrustedRuntimeClock,
)


RESULT_VERSION = "runtime-executor-result/1.0"
_OPAQUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class DurableExecutionError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class VerifiedExecutorInput:
    reference: ArtifactReference
    payload: bytes


@dataclass(frozen=True, slots=True)
class VerifiedExecutorInputBundle:
    workspace_observation_sha256: HashDigest
    bundle_sha256: HashDigest
    inputs: tuple[VerifiedExecutorInput, ...]


class FixtureExecutorPort(Protocol):
    adapter_id: OpaqueId

    def dispatch(
        self,
        request: RequestEnvelope,
        scope: ExecutorAuthorityScope,
        *,
        credential_lease: CredentialLease,
        input_bundle: VerifiedExecutorInputBundle,
    ) -> ResultEnvelope: ...

    def reconcile(
        self,
        request: RequestEnvelope,
        scope: ExecutorAuthorityScope,
        external_reference: ExternalReference | None,
        *,
        credential_lease: CredentialLease,
        input_bundle: VerifiedExecutorInputBundle,
    ) -> ResultEnvelope: ...


@dataclass(frozen=True, slots=True)
class DurableDispatchInputs:
    request: RequestEnvelope
    descriptor: CapabilityDescriptor
    orchestration_authorization: OrchestrationAuthorization
    current_context: GateContext
    workspace_observation: WorkspaceObservation
    expected_workspace_id: OpaqueId
    expected_workspace_revision_id: OpaqueId
    expected_workspace_revision: WorkspaceRevision
    expected_workspace_revision_sha256: HashDigest
    authority_request: ActionAuthorityRequest
    authority_decision: AuthorityDecision
    authority_ledger: ClaimBoundAuthorizationLedger
    service_identity: OpaqueId
    authority_scope: ExecutorAuthorityScope
    credential_reference: ArtifactReference


class DurableExecutorRuntime:
    def __init__(
        self,
        boundary: FixtureRuntimeBoundary,
        *,
        journal: SQLiteExecutionJournal,
        observer: FixtureWorkspaceObserver,
        executor: FixtureExecutorPort,
        identity_attestor: RuntimeIdentityAttestor,
        kill_switch: RuntimeKillSwitch,
        credential_broker: TrustedCredentialBroker,
        settlement: AuthorityReservationSettlementPort,
        clock: TrustedRuntimeClock,
    ) -> None:
        self._boundary = boundary
        self._journal = journal
        self._observer = observer
        self._executor = executor
        self._identity = identity_attestor
        self._kill_switch = kill_switch
        self._credentials = credential_broker
        self._settlement = settlement
        self._clock = clock
        self._result_directory = boundary.require_directory("executor-results", create=True)
        self._recovery_directory = boundary.require_directory(
            "executor-recovery", create=True
        )

    @staticmethod
    def _credential_scope(
        inputs: DurableDispatchInputs, purpose: VerificationPurpose
    ) -> CredentialScope:
        destination = inputs.authority_scope.destination
        if destination is None:
            raise DurableExecutionError(
                "runtime.executor.credential_destination",
                "credential scope requires an exact destination",
            )
        return CredentialScope(
            request_sha256=HashDigest(request_envelope_sha256(inputs.request)),
            plan_sha256=inputs.authority_request.executable_plan_sha256,
            workspace_id=inputs.expected_workspace_id,
            channel_id=inputs.authority_request.scope.channel_id,
            concept_id=inputs.authority_request.scope.concept_id,
            episode_id=inputs.authority_request.scope.episode_id,
            service_identity=inputs.service_identity,
            action_id=inputs.authority_request.action_id,
            adapter_id=inputs.descriptor.adapter_id,
            variant_id=(
                OpaqueId(inputs.authority_scope.model_id)
                if inputs.authority_scope.model_id is not None
                else None
            ),
            destination=destination,
            purpose=purpose,
        )

    @staticmethod
    def _credential_lease_id(reference: ArtifactReference) -> OpaqueId:
        return OpaqueId(f"credential-ref-{str(reference.sha256)[:24]}")

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise DurableExecutionError(
                "runtime.executor.clock", "trusted runtime clock returned a naive time"
            )
        return value

    @staticmethod
    def _reference_mapping(reference: ArtifactReference) -> dict[str, str]:
        require_managed_path(reference.path)
        if re.fullmatch(r"[0-9a-f]{64}", str(reference.sha256)) is None:
            raise DurableExecutionError(
                "runtime.executor.output_digest", "output reference digest is invalid"
            )
        return {
            "path": str(reference.path),
            "sha256": str(reference.sha256),
            "artifact_version": str(reference.artifact_version),
        }

    def _verified_input_bundle(
        self,
        inputs: DurableDispatchInputs,
        observation: WorkspaceObservation,
    ) -> VerifiedExecutorInputBundle:
        verified: list[VerifiedExecutorInput] = []
        for reference in inputs.request.input_artifacts:
            stable, payload = read_stable_regular_bytes(
                self._observer.managed_path(str(reference.path))
            )
            if stable.exact_sha256 != reference.sha256:
                raise DurableExecutionError(
                    "runtime.executor.input_rebound",
                    "effect input bytes differ from the request reference",
                )
            verified.append(VerifiedExecutorInput(reference, payload))
        observation_sha = workspace_observation_sha256(observation)
        bundle_sha = canonical_sha256(
            {
                "artifact_version": "verified-executor-input-bundle/1.0",
                "workspace_observation_sha256": str(observation_sha),
                "inputs": [
                    {
                        "path": str(item.reference.path),
                        "sha256": str(item.reference.sha256),
                        "artifact_version": str(item.reference.artifact_version),
                        "byte_length": len(item.payload),
                    }
                    for item in verified
                ],
            }
        )
        return VerifiedExecutorInputBundle(
            observation_sha,
            bundle_sha,
            tuple(verified),
        )

    @classmethod
    def _result_mapping(cls, result: ResultEnvelope) -> dict[str, object]:
        external = result.external_reference
        return {
            "artifact_version": RESULT_VERSION,
            "request_id": str(result.request_id),
            "outcome": result.outcome.value,
            "outputs": [cls._reference_mapping(item) for item in result.outputs],
            "external_reference": (
                {
                    "request_id": external.request_id,
                    "session_id": external.session_id,
                }
                if external is not None
                else None
            ),
            "measured_cost": {
                "amount": (
                    str(result.measured_cost.amount)
                    if result.measured_cost.amount is not None
                    else None
                ),
                "unit": result.measured_cost.unit,
                "is_unknown": result.measured_cost.is_unknown,
            },
            "uncertainty": {
                "uncertain": result.uncertainty.uncertain,
                "reason": result.uncertainty.reason,
                "reconcile_evidence": (
                    cls._reference_mapping(result.uncertainty.reconcile_evidence)
                    if result.uncertainty.reconcile_evidence is not None
                    else None
                ),
            },
        }

    def _persist_result(self, result: ResultEnvelope) -> ArtifactReference:
        mapping = self._result_mapping(result)
        payload = canonical_json_bytes(mapping)
        digest = HashDigest(hashlib.sha256(payload).hexdigest())
        path = self._result_directory / f"{digest}.json"
        if path.exists():
            stable = read_stable_regular_file(path)
            if stable.exact_sha256 != digest or stable.byte_length != len(payload):
                raise DurableExecutionError(
                    "runtime.executor.result_conflict",
                    "durable executor result differs for the same digest",
                )
        else:
            _write_new(path, payload)
        return ArtifactReference(
            RelativeArtifactPath(f"runtime-results/{digest}.json"),
            digest,
            ArtifactVersion(RESULT_VERSION),
        )

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
                raise DurableExecutionError(
                    "runtime.executor.artifact_conflict",
                    f"durable executor artifact differs: {file_name}",
                )
        else:
            _write_new(path, payload)
        return ArtifactReference(
            RelativeArtifactPath(logical_path),
            digest,
            ArtifactVersion(artifact_version),
        )

    @staticmethod
    def _decode_reference(mapping: dict[str, object]) -> ArtifactReference:
        return ArtifactReference(
            RelativeArtifactPath(str(mapping["path"])),
            HashDigest(str(mapping["sha256"])),
            ArtifactVersion(str(mapping["artifact_version"])),
        )

    def _load_result_from_receipt(
        self, receipt: ExecutionReceipt | None
    ) -> ResultEnvelope:
        if receipt is None:
            raise DurableExecutionError(
                "runtime.executor.result_missing", "journal has no durable executor result"
            )
        references = tuple(
            item
            for item in receipt.output_refs
            if str(item.artifact_version) == RESULT_VERSION
        )
        if len(references) != 1:
            raise DurableExecutionError(
                "runtime.executor.result_set", "journal result evidence is missing or ambiguous"
            )
        reference = references[0]
        path = self._result_directory / Path(str(reference.path)).name
        stable, payload = read_stable_regular_bytes(path)
        if stable.exact_sha256 != reference.sha256:
            raise DurableExecutionError(
                "runtime.executor.result_rebound", "durable executor result was changed"
            )
        try:
            mapping = json.loads(payload)
            if payload != canonical_json_bytes(mapping):
                raise ValueError("noncanonical result")
            external_mapping = mapping["external_reference"]
            cost_mapping = mapping["measured_cost"]
            uncertainty_mapping = mapping["uncertainty"]
            assert isinstance(cost_mapping, dict) and isinstance(uncertainty_mapping, dict)
            assert external_mapping is None or isinstance(external_mapping, dict)
            reconcile_mapping = uncertainty_mapping["reconcile_evidence"]
            result = ResultEnvelope(
                request_id=RequestId(str(mapping["request_id"])),
                outcome=Outcome(str(mapping["outcome"])),
                outputs=tuple(
                    self._decode_reference(item) for item in mapping["outputs"]
                ),
                external_reference=(
                    ExternalReference(
                        external_mapping["request_id"], external_mapping["session_id"]
                    )
                    if external_mapping is not None
                    else None
                ),
                measured_cost=CostMeasurement(
                    (
                        Decimal(str(cost_mapping["amount"]))
                        if cost_mapping["amount"] is not None
                        else None
                    ),
                    cost_mapping["unit"],
                    bool(cost_mapping["is_unknown"]),
                ),
                uncertainty=UncertaintyEvidence(
                    bool(uncertainty_mapping["uncertain"]),
                    uncertainty_mapping["reason"],
                    (
                        self._decode_reference(reconcile_mapping)
                        if isinstance(reconcile_mapping, dict)
                        else None
                    ),
                ),
            )
        except Exception as error:
            raise DurableExecutionError(
                "runtime.executor.result_corrupt", "durable executor result is invalid"
            ) from error
        return result

    def _load_result(self, snapshot: JournalSnapshot) -> ResultEnvelope:
        return self._load_result_from_receipt(snapshot.receipt)

    def _load_latest_historical_result(
        self, snapshot: JournalSnapshot
    ) -> ResultEnvelope | None:
        if snapshot.receipt is not None:
            return self._load_result(snapshot)
        for receipt in reversed(self._journal.receipt_history(snapshot.journal_id)):
            if any(
                str(reference.artifact_version) == RESULT_VERSION
                for reference in receipt.output_refs
            ):
                return self._load_result_from_receipt(receipt)
        return None

    @staticmethod
    def _allowed_output(request: RequestEnvelope, reference: ArtifactReference) -> bool:
        path = str(reference.path)
        return any(
            (
                path == str(rule.path_prefix)
                or path.startswith(f"{rule.path_prefix}/")
            )
            and reference.artifact_version in rule.artifact_versions
            for rule in request.allowed_outputs
        )

    def _validate_result(
        self,
        inputs: DurableDispatchInputs,
        result: ResultEnvelope,
        before: WorkspaceObservation,
        after: WorkspaceObservation,
    ) -> int | None:
        if result.request_id != inputs.request.request_id:
            raise DurableExecutionError(
                "runtime.executor.result_request", "executor result belongs to another request"
            )
        if not isinstance(result.outcome, Outcome):
            raise DurableExecutionError(
                "runtime.executor.result_outcome", "executor outcome is invalid"
            )
        identities: dict[str, tuple[str, str, str]] = {}
        output_paths: set[str] = set()
        for reference in result.outputs:
            self._reference_mapping(reference)
            key = path_collision_key(reference.path)
            identity = (
                str(reference.path),
                str(reference.sha256),
                str(reference.artifact_version),
            )
            previous = identities.get(key)
            if previous is not None and previous != identity:
                raise DurableExecutionError(
                    "runtime.executor.output_alias", "executor outputs contain a path alias"
                )
            identities[key] = identity
            if not self._allowed_output(inputs.request, reference):
                raise DurableExecutionError(
                    "runtime.executor.output_scope", "executor output is outside allowed scope"
                )
            self._observer.require_exact_file(
                str(reference.path), expected_sha256=reference.sha256
            )
            output_paths.add(str(reference.path))
        if result.outcome is not Outcome.SUCCEEDED and result.outputs:
            raise DurableExecutionError(
                "runtime.executor.failed_outputs",
                "non-success executor result cannot claim completed outputs",
            )
        if result.outcome is Outcome.AWAITING_HUMAN:
            raise DurableExecutionError(
                "runtime.executor.awaiting_human", "runtime executor cannot return human handoff"
            )
        if result.outcome is Outcome.EXTERNAL_UNCERTAIN:
            if not result.uncertainty.uncertain:
                raise DurableExecutionError(
                    "runtime.executor.uncertainty", "uncertain outcome lacks uncertainty evidence"
                )
        elif (
            result.uncertainty.uncertain
            or result.uncertainty.reason is not None
            or result.uncertainty.reconcile_evidence is not None
        ):
            raise DurableExecutionError(
                "runtime.executor.uncertainty", "settled outcome carries uncertainty evidence"
            )
        if result.external_reference is not None:
            for value in (
                result.external_reference.request_id,
                result.external_reference.session_id,
            ):
                if value is not None and _OPAQUE.fullmatch(value) is None:
                    raise DurableExecutionError(
                        "runtime.executor.external_reference",
                        "external reference is not an opaque identifier",
                    )
        cost = result.measured_cost
        measured_minor: int | None
        if cost.is_unknown:
            if cost.amount is not None or cost.unit is not None:
                raise DurableExecutionError(
                    "runtime.executor.cost", "unknown cost cannot carry amount or unit"
                )
            measured_minor = None
        else:
            if (
                not isinstance(cost.amount, Decimal)
                or cost.amount < 0
                or cost.amount != cost.amount.to_integral_value()
                or cost.unit != inputs.authority_scope.currency
            ):
                raise DurableExecutionError(
                    "runtime.executor.cost", "measured cost is invalid or in another currency"
                )
            measured_minor = int(cost.amount)
            if measured_minor > inputs.authority_scope.cost_minor_units:
                raise DurableExecutionError(
                    "runtime.executor.cost_overrun", "measured cost exceeds reserved scope"
                )
        before_map = {str(item.path): item for item in before.entries}
        after_map = {str(item.path): item for item in after.entries}
        changed = {
            path
            for path in set(before_map) | set(after_map)
            if before_map.get(path) != after_map.get(path)
        }
        allowed_changed = set(output_paths)
        for output_path in output_paths:
            parts = output_path.split("/")
            allowed_changed.update(
                "/".join(parts[:index]) for index in range(1, len(parts))
            )
        if not changed.issubset(allowed_changed):
            raise DurableExecutionError(
                "runtime.executor.unreported_change",
                "executor changed workspace paths outside its exact output set",
            )
        return measured_minor

    @staticmethod
    def _external_id(result: ResultEnvelope) -> OpaqueId | None:
        if result.external_reference is None:
            return None
        digest = canonical_sha256(
            {
                "request_id": result.external_reference.request_id,
                "session_id": result.external_reference.session_id,
            }
        )
        return OpaqueId(f"external-{str(digest)[:20]}")

    @staticmethod
    def _reservation_sha256(
        verified: object,
        *,
        runtime_claim_sha256: HashDigest,
        expected_purpose: VerificationPurpose,
    ) -> HashDigest:
        try:
            return runtime_authority_reservation_sha256(
                verified,
                runtime_claim_sha256=runtime_claim_sha256,
                expected_purpose=expected_purpose,
            )
        except ValueError as error:
            raise DurableExecutionError(
                "runtime.executor.authority_receipt",
                "fresh authority verification returned an invalid receipt",
            ) from error

    def _current_identity(
        self,
        service_identity: OpaqueId,
        *,
        evaluated_at: datetime,
        expected: ArtifactReference | None = None,
    ) -> ArtifactReference:
        attestation = self._identity.attest_current(
            service_identity, evaluated_at=evaluated_at
        )
        if attestation is None or (expected is not None and attestation != expected):
            raise DurableExecutionError(
                "runtime.executor.identity",
                "current service identity is not attested to the exact runtime intent",
            )
        return attestation

    def _require_switch_clear(self, *, evaluated_at: datetime) -> None:
        if self._kill_switch.engaged(evaluated_at=evaluated_at):
            raise DurableExecutionError(
                "runtime.executor.kill_switch", "runtime kill switch is engaged"
            )

    def _reconciliation_reference(
        self,
        snapshot: JournalSnapshot,
        *,
        result_reference: ArtifactReference,
        before_workspace_sha256: HashDigest,
        after_workspace_sha256: HashDigest,
        authority_receipt_sha256: HashDigest,
        reservation_sha256: HashDigest,
        service_identity_attestation: ArtifactReference,
        completed_at: str,
        settled: bool,
        reason_codes: tuple[str, ...],
        external_reference_id: OpaqueId | None,
    ) -> ArtifactReference:
        version = "runtime-executor-reconciliation/1.0"
        mapping = {
            "artifact_version": version,
            "journal_id": str(snapshot.journal_id),
            "intent_sha256": str(snapshot.intent.intent_sha256),
            "result": self._reference_mapping(result_reference),
            "before_workspace_observation_sha256": str(before_workspace_sha256),
            "after_workspace_observation_sha256": str(after_workspace_sha256),
            "authority_receipt_sha256": str(authority_receipt_sha256),
            "reservation_sha256": str(reservation_sha256),
            "service_identity_attestation": self._reference_mapping(
                service_identity_attestation
            ),
            "external_reference_id": (
                str(external_reference_id)
                if external_reference_id is not None
                else None
            ),
            "completed_at": completed_at,
            "settled": settled,
            "redispatch_performed": False,
            "reason_codes": list(tuple(sorted(set(reason_codes)))),
            "authority_effect": "none",
        }
        digest = canonical_sha256(mapping)
        return self._persist_mapping(
            self._recovery_directory,
            file_name=f"{snapshot.journal_id}-{str(digest)[:16]}.json",
            logical_path=f"runtime-recovery/{snapshot.journal_id}-{str(digest)[:16]}.json",
            artifact_version=version,
            mapping=mapping,
        )

    def _finish(
        self,
        snapshot: JournalSnapshot,
        *,
        result: ResultEnvelope,
        final_state: JournalState,
        before: WorkspaceObservation,
        after: WorkspaceObservation,
        authority_receipt_sha256: HashDigest,
        reservation_sha256: HashDigest,
        settlement_record: ArtifactReference | None,
        reconciliation_record: ArtifactReference | None,
        audit_refs: tuple[ArtifactReference, ...] = (),
        measured_cost_minor_units: int | None,
        currency: str | None,
        completed_at: datetime,
        reason_codes: tuple[str, ...],
    ) -> ResultEnvelope:
        result_ref = self._persist_result(result)
        output_refs = tuple(
            sorted(
                (result_ref, *result.outputs, *audit_refs),
                key=lambda item: (
                    str(item.path), str(item.sha256), str(item.artifact_version)
                ),
            )
        )
        self._journal.finish(
            snapshot.journal_id,
            final_state,
            occurred_at=completed_at.isoformat(),
            reason_codes=tuple(sorted(set(reason_codes))),
            external_reference_id=self._external_id(result),
            authority_receipt_sha256=authority_receipt_sha256,
            workspace_observation_sha256=workspace_observation_sha256(after),
            reservation_sha256=reservation_sha256,
            receipt_factory=lambda event: build_execution_receipt(
                intent_sha256=snapshot.intent.intent_sha256,
                journal_id=snapshot.journal_id,
                journal_head_sha256=event.event_sha256,
                final_state=final_state,
                started_at=snapshot.intent.created_at,
                completed_at=completed_at.isoformat(),
                output_refs=output_refs,
                measured_cost_minor_units=measured_cost_minor_units,
                currency=currency,
                before_workspace_observation_sha256=workspace_observation_sha256(
                    before
                ),
                after_workspace_observation_sha256=workspace_observation_sha256(after),
                authority_receipt_sha256=authority_receipt_sha256,
                settlement_record=settlement_record,
                reconciliation_record=reconciliation_record,
                reason_codes=tuple(sorted(set(reason_codes))),
            ),
        )
        return result

    def _uncertain_result(
        self, request: RequestEnvelope, *, reason: str
    ) -> ResultEnvelope:
        return ResultEnvelope(
            request_id=request.request_id,
            outcome=Outcome.EXTERNAL_UNCERTAIN,
            outputs=(),
            external_reference=None,
            measured_cost=CostMeasurement(None, None, True),
            uncertainty=UncertaintyEvidence(True, reason, None),
        )

    def _require_reconcile_binding(
        self,
        snapshot: JournalSnapshot,
        inputs: DurableDispatchInputs,
    ) -> None:
        intent = snapshot.intent
        expected = (
            intent.action_kind is RuntimeActionKind.EXECUTOR,
            str(intent.action_id) == "executor_dispatch",
            str(intent.request_id) == str(inputs.request.request_id),
            intent.request_sha256
            == HashDigest(request_envelope_sha256(inputs.request)),
            intent.idempotency_key == inputs.request.idempotency_key,
            intent.workspace_id == inputs.expected_workspace_id,
            intent.plan_sha256 == inputs.authority_request.executable_plan_sha256,
            intent.gate_context_sha256 == gate_context_sha256(inputs.current_context),
            intent.workspace_observation_sha256
            == workspace_observation_sha256(inputs.workspace_observation),
            intent.authority_decision_sha256
            == inputs.authority_decision.decision_sha256,
            intent.service_identity == inputs.service_identity,
            intent.credential_handle_id
            == self._credential_lease_id(inputs.credential_reference),
            intent.credential_verification is not None,
            intent.destination == inputs.authority_scope.destination,
        )
        if not all(expected):
            raise DurableExecutionError(
                "runtime.executor.reconcile_rebound",
                "reconciliation inputs differ from the durable execution intent",
            )

    def dispatch(self, inputs: DurableDispatchInputs) -> ResultEnvelope:
        validate_request_envelope(inputs.request, inputs.descriptor, AdapterKind.EXECUTOR)
        adapter_matches = self._executor.adapter_id == inputs.descriptor.adapter_id
        if not adapter_matches:
            raise DurableExecutionError(
                "runtime.executor.adapter", "fixture executor identity differs from descriptor"
            )
        request_sha256 = HashDigest(request_envelope_sha256(inputs.request))
        existing = self._journal.load_by_scope(
            action_kind=RuntimeActionKind.EXECUTOR,
            action_id=OpaqueId("executor_dispatch"),
            idempotency_key=inputs.request.idempotency_key,
            request_sha256=request_sha256,
        )
        if existing is not None:
            self._require_reconcile_binding(existing, inputs)
            if existing.receipt is not None:
                return self._load_result(existing)
            if existing.reconcile_only:
                raise DurableExecutionError(
                    "runtime.executor.reconcile_required",
                    "may-have-started executor work requires reconciliation",
                )
        now = self._aware(self._clock.now())
        attestation = self._current_identity(
            inputs.service_identity, evaluated_at=now
        )
        initial_credential = require_current_credential_lease(
            self._credentials.verify_current(
                inputs.credential_reference,
                self._credential_scope(inputs, VerificationPurpose.DISPATCH),
                evaluated_at=now,
            ),
            inputs.credential_reference,
            self._credential_scope(inputs, VerificationPurpose.DISPATCH),
            evaluated_at=now,
        )
        intent = build_execution_intent(
            request_id=OpaqueId(str(inputs.request.request_id)),
            request_sha256=request_sha256,
            idempotency_key=inputs.request.idempotency_key,
            action_kind=RuntimeActionKind.EXECUTOR,
            action_id=OpaqueId("executor_dispatch"),
            planned_effect_ids=(),
            workspace_id=inputs.expected_workspace_id,
            plan_sha256=inputs.authority_request.executable_plan_sha256,
            gate_context_sha256=gate_context_sha256(inputs.current_context),
            workspace_observation_sha256=workspace_observation_sha256(
                inputs.workspace_observation
            ),
            authority_decision_sha256=inputs.authority_decision.decision_sha256,
            authority_receipt_sha256=None,
            service_identity=inputs.service_identity,
            service_identity_attestation=attestation,
            credential_handle_id=self._credential_lease_id(
                inputs.credential_reference
            ),
            credential_verification=initial_credential.attestation_record,
            destination=inputs.authority_scope.destination,
            created_at=inputs.authority_decision.evaluated_at,
        )
        snapshot, created = self._journal.reserve_planned(
            intent, occurred_at=inputs.authority_decision.evaluated_at
        )
        if not created:
            if snapshot.receipt is not None:
                return self._load_result(snapshot)
            if snapshot.reconcile_only:
                raise DurableExecutionError(
                    "runtime.executor.reconcile_required",
                    "may-have-started executor work requires reconciliation",
                )
            if snapshot.state not in {
                JournalState.PLANNED,
                JournalState.AUTHORIZED,
                JournalState.RESERVED,
            }:
                raise DurableExecutionError(
                    "runtime.executor.terminal_receipt_missing",
                    "terminal executor journal is missing its durable receipt",
                )
        before = self._observer.observe(
            revision_id=inputs.expected_workspace_revision_id
        )
        if before != inputs.workspace_observation:
            raise DurableExecutionError(
                "runtime.executor.workspace_rebound",
                "runtime workspace differs from the exact dispatch observation",
            )
        evaluated_at = self._aware(self._clock.now())
        self._require_switch_clear(evaluated_at=evaluated_at)
        claim = self._journal.prepare_reservation_claim(
            snapshot.journal_id,
            purpose=VerificationPurpose.DISPATCH,
            effect_id=OpaqueId("executor-dispatch"),
            expected_head_sha256=self._journal.load(
                snapshot.journal_id
            ).events[-1].event_sha256,
            occurred_at=evaluated_at.isoformat(),
        )
        verified = verify_adapter_dispatch_authority(
            inputs.request,
            inputs.descriptor,
            AdapterKind.EXECUTOR,
            authorization=inputs.orchestration_authorization,
            current_context=inputs.current_context,
            evaluated_at=evaluated_at,
            workspace_observation=before,
            expected_workspace_id=str(inputs.expected_workspace_id),
            expected_workspace_revision_id=inputs.expected_workspace_revision_id,
            expected_workspace_revision=inputs.expected_workspace_revision,
            expected_workspace_revision_sha256=str(
                inputs.expected_workspace_revision_sha256
            ),
            authority_request=inputs.authority_request,
            authority_decision=inputs.authority_decision,
            authority_ledger=ClaimBoundLedgerAdapter(
                inputs.authority_ledger, claim.claim_sha256
            ),
            service_identity=str(inputs.service_identity),
            executor_authority_scope=inputs.authority_scope,
            verification_purpose=VerificationPurpose.DISPATCH,
        )
        reservation_sha = self._reservation_sha256(
            verified,
            runtime_claim_sha256=claim.claim_sha256,
            expected_purpose=VerificationPurpose.DISPATCH,
        )
        self._journal.record_reservation_result(
            claim,
            authority_receipt_sha256=verified.receipt.receipt_sha256,
            reservation_sha256=reservation_sha,
            verified_at=evaluated_at.isoformat(),
        )
        marker_at = self._aware(self._clock.now())
        self._current_identity(
            inputs.service_identity,
            evaluated_at=marker_at,
            expected=snapshot.intent.service_identity_attestation,
        )
        self._require_switch_clear(evaluated_at=marker_at)
        current_credential = require_current_credential_lease(
            self._credentials.verify_current(
                inputs.credential_reference,
                self._credential_scope(inputs, VerificationPurpose.DISPATCH),
                evaluated_at=marker_at,
            ),
            inputs.credential_reference,
            self._credential_scope(inputs, VerificationPurpose.DISPATCH),
            evaluated_at=marker_at,
        )
        current_state = snapshot.state
        if current_state in {JournalState.PLANNED, JournalState.AUTHORIZED}:
            self._journal.append_state(
                snapshot.journal_id,
                JournalState.AUTHORIZED,
                occurred_at=evaluated_at.isoformat(),
                authority_receipt_sha256=verified.receipt.receipt_sha256,
                workspace_observation_sha256=workspace_observation_sha256(before),
            )
            current_state = JournalState.AUTHORIZED
        if current_state is JournalState.AUTHORIZED:
            self._journal.append_state(
                snapshot.journal_id,
                JournalState.RESERVED,
                occurred_at=marker_at.isoformat(),
                reservation_sha256=reservation_sha,
            )
        elif current_state is JournalState.RESERVED:
            self._journal.append_state(
                snapshot.journal_id,
                JournalState.RESERVED,
                occurred_at=marker_at.isoformat(),
                authority_receipt_sha256=verified.receipt.receipt_sha256,
                workspace_observation_sha256=workspace_observation_sha256(before),
                reservation_sha256=reservation_sha,
            )
        dispatch_head = self._journal.load(snapshot.journal_id).events[-1].event_sha256
        self._journal.claim_dispatch(
            snapshot.journal_id,
            expected_head_sha256=dispatch_head,
            occurred_at=marker_at.isoformat(),
            authority_receipt_sha256=verified.receipt.receipt_sha256,
            workspace_observation_sha256=workspace_observation_sha256(before),
            reservation_sha256=reservation_sha,
        )
        effect_at = self._aware(self._clock.now())
        self._current_identity(
            inputs.service_identity,
            evaluated_at=effect_at,
            expected=snapshot.intent.service_identity_attestation,
        )
        self._require_switch_clear(evaluated_at=effect_at)
        effect_credential = require_current_credential_lease(
            self._credentials.verify_current(
                inputs.credential_reference,
                self._credential_scope(inputs, VerificationPurpose.DISPATCH),
                evaluated_at=effect_at,
            ),
            inputs.credential_reference,
            self._credential_scope(inputs, VerificationPurpose.DISPATCH),
            evaluated_at=effect_at,
        )
        effect_workspace = self._observer.observe(
            revision_id=inputs.expected_workspace_revision_id
        )
        if effect_workspace != before:
            raise DurableExecutionError(
                "runtime.executor.workspace_rebound",
                "workspace changed after the durable dispatch marker",
            )
        effect_verified = verify_adapter_dispatch_authority(
            inputs.request,
            inputs.descriptor,
            AdapterKind.EXECUTOR,
            authorization=inputs.orchestration_authorization,
            current_context=inputs.current_context,
            evaluated_at=effect_at,
            workspace_observation=effect_workspace,
            expected_workspace_id=str(inputs.expected_workspace_id),
            expected_workspace_revision_id=inputs.expected_workspace_revision_id,
            expected_workspace_revision=inputs.expected_workspace_revision,
            expected_workspace_revision_sha256=str(
                inputs.expected_workspace_revision_sha256
            ),
            authority_request=inputs.authority_request,
            authority_decision=inputs.authority_decision,
            authority_ledger=ClaimBoundLedgerAdapter(
                inputs.authority_ledger, claim.claim_sha256
            ),
            service_identity=str(inputs.service_identity),
            executor_authority_scope=inputs.authority_scope,
            verification_purpose=VerificationPurpose.DISPATCH,
        )
        effect_reservation_sha = self._reservation_sha256(
            effect_verified,
            runtime_claim_sha256=claim.claim_sha256,
            expected_purpose=VerificationPurpose.DISPATCH,
        )
        if effect_reservation_sha != reservation_sha:
            raise DurableExecutionError(
                "runtime.executor.authority_rebound",
                "effect-time authority differs from the durable reservation claim",
            )
        self._journal.record_reservation_result(
            claim,
            authority_receipt_sha256=effect_verified.receipt.receipt_sha256,
            reservation_sha256=effect_reservation_sha,
            verified_at=effect_at.isoformat(),
        )
        input_bundle = self._verified_input_bundle(inputs, effect_workspace)
        try:
            result = self._executor.dispatch(
                inputs.request,
                inputs.authority_scope,
                credential_lease=effect_credential,
                input_bundle=input_bundle,
            )
        except ExternalStateUncertain as error:
            result = ResultEnvelope(
                request_id=inputs.request.request_id,
                outcome=Outcome.EXTERNAL_UNCERTAIN,
                outputs=(),
                external_reference=error.external_reference,
                measured_cost=CostMeasurement(None, None, True),
                uncertainty=UncertaintyEvidence(True, str(error), None),
            )
        except Exception as error:
            result = self._uncertain_result(
                inputs.request, reason=f"{type(error).__name__}:dispatch"
            )
        dispatch_completed_at = self._aware(self._clock.now())
        self._journal.append_state(
            snapshot.journal_id,
            JournalState.DISPATCHED,
            occurred_at=dispatch_completed_at.isoformat(),
            external_reference_id=self._external_id(result),
        )
        after = self._observer.observe(
            revision_id=inputs.expected_workspace_revision_id
        )
        try:
            measured_minor = self._validate_result(inputs, result, before, after)
            requested_final = (
                JournalState.SUCCEEDED
                if result.outcome is Outcome.SUCCEEDED
                else JournalState.UNCERTAIN
                if result.outcome is Outcome.EXTERNAL_UNCERTAIN
                else JournalState.FAILED
            )
            reason_codes = (
                ()
                if requested_final is JournalState.SUCCEEDED
                else (f"runtime.executor.{result.outcome.value.lower()}",)
            )
        except Exception as error:
            measured_minor = None
            requested_final = JournalState.UNCERTAIN
            reason_codes = (
                getattr(error, "reason_code", "runtime.executor.result_invalid"),
            )
            result = self._uncertain_result(
                inputs.request, reason=reason_codes[0]
            )
        completed_at = self._aware(self._clock.now())
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
            raise DurableExecutionError(
                "runtime.executor.settlement_reservation",
                "an unsettled reservation claim lacks its trusted ledger result",
            )
        settlement = self._settlement.finalize_current(
            journal_id=snapshot.journal_id,
            reservation_claim_sha256s=tuple(
                item.claim_sha256 for item in settlement_claims
            ),
            reservation_sha256s=settlement_reservations,
            final_state=requested_final,
            evaluated_at=completed_at,
        )
        if settlement is None:
            requested_final = JournalState.UNCERTAIN
            reason_codes = ("runtime.executor.settlement_missing",)
            measured_minor = None
            result = self._uncertain_result(
                inputs.request, reason="runtime.executor.settlement_missing"
            )
        else:
            self._journal.record_claim_settlement(
                settlement_claims,
                settlement_record=settlement,
                final_state=requested_final,
            )
        return self._finish(
            snapshot,
            result=result,
            final_state=requested_final,
            before=before,
            after=after,
            authority_receipt_sha256=verified.receipt.receipt_sha256,
            reservation_sha256=reservation_sha,
            settlement_record=settlement,
            reconciliation_record=None,
            measured_cost_minor_units=measured_minor,
            currency=(
                inputs.authority_scope.currency if measured_minor is not None else None
            ),
            completed_at=completed_at,
            reason_codes=reason_codes,
        )

    def reconcile_pending(
        self,
        journal_id: OpaqueId,
        inputs: DurableDispatchInputs,
    ) -> ResultEnvelope:
        """Resolve may-have-started work without ever invoking dispatch again."""

        validate_request_envelope(inputs.request, inputs.descriptor, AdapterKind.EXECUTOR)
        adapter_matches = self._executor.adapter_id == inputs.descriptor.adapter_id
        if not adapter_matches:
            raise DurableExecutionError(
                "runtime.executor.adapter", "fixture executor identity differs from descriptor"
            )
        snapshot = self._journal.load(journal_id)
        if not snapshot.reconcile_only:
            raise DurableExecutionError(
                "runtime.executor.reconcile_state",
                "only may-have-started executor work can be reconciled",
            )
        self._require_reconcile_binding(snapshot, inputs)
        prior_result = self._load_latest_historical_result(snapshot)
        external_reference = (
            prior_result.external_reference if prior_result is not None else None
        )
        before = self._observer.observe(
            revision_id=inputs.expected_workspace_revision_id
        )
        if before != inputs.workspace_observation:
            raise DurableExecutionError(
                "runtime.executor.workspace_rebound",
                "runtime workspace differs from the exact reconciliation observation",
            )
        evaluated_at = self._aware(self._clock.now())
        attestation = self._current_identity(
            inputs.service_identity, evaluated_at=evaluated_at
        )
        self._require_switch_clear(evaluated_at=evaluated_at)
        claim = self._journal.prepare_reservation_claim(
            snapshot.journal_id,
            purpose=VerificationPurpose.RECONCILE,
            effect_id=OpaqueId(
                "executor-reconcile-"
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
        verified = verify_adapter_dispatch_authority(
            inputs.request,
            inputs.descriptor,
            AdapterKind.EXECUTOR,
            authorization=inputs.orchestration_authorization,
            current_context=inputs.current_context,
            evaluated_at=evaluated_at,
            workspace_observation=before,
            expected_workspace_id=str(inputs.expected_workspace_id),
            expected_workspace_revision_id=inputs.expected_workspace_revision_id,
            expected_workspace_revision=inputs.expected_workspace_revision,
            expected_workspace_revision_sha256=str(
                inputs.expected_workspace_revision_sha256
            ),
            authority_request=inputs.authority_request,
            authority_decision=inputs.authority_decision,
            authority_ledger=ClaimBoundLedgerAdapter(
                inputs.authority_ledger, claim.claim_sha256
            ),
            service_identity=str(inputs.service_identity),
            executor_authority_scope=inputs.authority_scope,
            verification_purpose=VerificationPurpose.RECONCILE,
        )
        reservation_sha = self._reservation_sha256(
            verified,
            runtime_claim_sha256=claim.claim_sha256,
            expected_purpose=VerificationPurpose.RECONCILE,
        )
        self._journal.record_reservation_result(
            claim,
            authority_receipt_sha256=verified.receipt.receipt_sha256,
            reservation_sha256=reservation_sha,
            verified_at=evaluated_at.isoformat(),
        )
        marker_at = self._aware(self._clock.now())
        self._current_identity(
            inputs.service_identity,
            evaluated_at=marker_at,
            expected=attestation,
        )
        self._require_switch_clear(evaluated_at=marker_at)
        current_credential = require_current_credential_lease(
            self._credentials.verify_current(
                inputs.credential_reference,
                self._credential_scope(inputs, VerificationPurpose.RECONCILE),
                evaluated_at=marker_at,
            ),
            inputs.credential_reference,
            self._credential_scope(inputs, VerificationPurpose.RECONCILE),
            evaluated_at=marker_at,
        )
        self._journal.append_state(
            snapshot.journal_id,
            JournalState.RECONCILING,
            occurred_at=marker_at.isoformat(),
            reason_codes=("runtime.executor.reconcile_started",),
            external_reference_id=(
                self._external_id(prior_result) if prior_result is not None else None
            ),
            authority_receipt_sha256=verified.receipt.receipt_sha256,
            workspace_observation_sha256=workspace_observation_sha256(before),
            reservation_sha256=reservation_sha,
        )
        effect_at = self._aware(self._clock.now())
        self._current_identity(
            inputs.service_identity,
            evaluated_at=effect_at,
            expected=attestation,
        )
        self._require_switch_clear(evaluated_at=effect_at)
        effect_credential = require_current_credential_lease(
            self._credentials.verify_current(
                inputs.credential_reference,
                self._credential_scope(inputs, VerificationPurpose.RECONCILE),
                evaluated_at=effect_at,
            ),
            inputs.credential_reference,
            self._credential_scope(inputs, VerificationPurpose.RECONCILE),
            evaluated_at=effect_at,
        )
        effect_workspace = self._observer.observe(
            revision_id=inputs.expected_workspace_revision_id
        )
        if effect_workspace != before:
            raise DurableExecutionError(
                "runtime.executor.workspace_rebound",
                "workspace changed after the reconciliation marker",
            )
        effect_verified = verify_adapter_dispatch_authority(
            inputs.request,
            inputs.descriptor,
            AdapterKind.EXECUTOR,
            authorization=inputs.orchestration_authorization,
            current_context=inputs.current_context,
            evaluated_at=effect_at,
            workspace_observation=effect_workspace,
            expected_workspace_id=str(inputs.expected_workspace_id),
            expected_workspace_revision_id=inputs.expected_workspace_revision_id,
            expected_workspace_revision=inputs.expected_workspace_revision,
            expected_workspace_revision_sha256=str(
                inputs.expected_workspace_revision_sha256
            ),
            authority_request=inputs.authority_request,
            authority_decision=inputs.authority_decision,
            authority_ledger=ClaimBoundLedgerAdapter(
                inputs.authority_ledger, claim.claim_sha256
            ),
            service_identity=str(inputs.service_identity),
            executor_authority_scope=inputs.authority_scope,
            verification_purpose=VerificationPurpose.RECONCILE,
        )
        effect_reservation_sha = self._reservation_sha256(
            effect_verified,
            runtime_claim_sha256=claim.claim_sha256,
            expected_purpose=VerificationPurpose.RECONCILE,
        )
        if effect_reservation_sha != reservation_sha:
            raise DurableExecutionError(
                "runtime.executor.authority_rebound",
                "reconcile-time authority differs from the durable reservation claim",
            )
        self._journal.record_reservation_result(
            claim,
            authority_receipt_sha256=effect_verified.receipt.receipt_sha256,
            reservation_sha256=effect_reservation_sha,
            verified_at=effect_at.isoformat(),
        )
        input_bundle = self._verified_input_bundle(inputs, effect_workspace)
        try:
            result = self._executor.reconcile(
                inputs.request,
                inputs.authority_scope,
                external_reference,
                credential_lease=effect_credential,
                input_bundle=input_bundle,
            )
        except ExternalStateUncertain as error:
            result = ResultEnvelope(
                request_id=inputs.request.request_id,
                outcome=Outcome.EXTERNAL_UNCERTAIN,
                outputs=(),
                external_reference=error.external_reference or external_reference,
                measured_cost=CostMeasurement(None, None, True),
                uncertainty=UncertaintyEvidence(True, str(error), None),
            )
        except Exception as error:
            result = ResultEnvelope(
                request_id=inputs.request.request_id,
                outcome=Outcome.EXTERNAL_UNCERTAIN,
                outputs=(),
                external_reference=external_reference,
                measured_cost=CostMeasurement(None, None, True),
                uncertainty=UncertaintyEvidence(
                    True, f"{type(error).__name__}:reconcile", None
                ),
            )
        after_call_at = self._aware(self._clock.now())
        after = self._observer.observe(
            revision_id=inputs.expected_workspace_revision_id
        )
        try:
            measured_minor = self._validate_result(inputs, result, before, after)
            settled = result.outcome is not Outcome.EXTERNAL_UNCERTAIN
            reason_codes = (
                ("runtime.executor.reconciled",)
                if settled
                else ("runtime.executor.reconcile_uncertain",)
            )
        except Exception as error:
            measured_minor = None
            settled = False
            reason_codes = (
                getattr(error, "reason_code", "runtime.executor.reconcile_invalid"),
            )
            result = ResultEnvelope(
                request_id=inputs.request.request_id,
                outcome=Outcome.EXTERNAL_UNCERTAIN,
                outputs=(),
                external_reference=external_reference,
                measured_cost=CostMeasurement(None, None, True),
                uncertainty=UncertaintyEvidence(True, reason_codes[0], None),
            )
        final_state = (
            JournalState.RECONCILED if settled else JournalState.UNCERTAIN
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
            raise DurableExecutionError(
                "runtime.executor.reconcile_reservation",
                "an unsettled reservation claim lacks its trusted ledger result",
            )
        settlement = self._settlement.finalize_current(
            journal_id=snapshot.journal_id,
            reservation_claim_sha256s=tuple(
                item.claim_sha256 for item in settlement_claims
            ),
            reservation_sha256s=settlement_reservations,
            final_state=final_state,
            evaluated_at=after_call_at,
        )
        if settlement is None:
            measured_minor = None
            settled = False
            final_state = JournalState.UNCERTAIN
            reason_codes = ("runtime.executor.reconcile_settlement_missing",)
            result = ResultEnvelope(
                request_id=inputs.request.request_id,
                outcome=Outcome.EXTERNAL_UNCERTAIN,
                outputs=(),
                external_reference=external_reference,
                measured_cost=CostMeasurement(None, None, True),
                uncertainty=UncertaintyEvidence(True, reason_codes[0], None),
            )
        else:
            self._journal.record_claim_settlement(
                settlement_claims,
                settlement_record=settlement,
                final_state=final_state,
            )
        result_reference = self._persist_result(result)
        reconciliation = self._reconciliation_reference(
            snapshot,
            result_reference=result_reference,
            before_workspace_sha256=workspace_observation_sha256(before),
            after_workspace_sha256=workspace_observation_sha256(after),
            authority_receipt_sha256=verified.receipt.receipt_sha256,
            reservation_sha256=reservation_sha,
            service_identity_attestation=attestation,
            completed_at=after_call_at.isoformat(),
            settled=settled,
            reason_codes=reason_codes,
            external_reference_id=self._external_id(result),
        )
        return self._finish(
            snapshot,
            result=result,
            final_state=final_state,
            before=before,
            after=after,
            authority_receipt_sha256=verified.receipt.receipt_sha256,
            reservation_sha256=reservation_sha,
            settlement_record=settlement,
            reconciliation_record=(
                reconciliation
                if final_state is JournalState.RECONCILED
                else None
            ),
            audit_refs=(reconciliation,),
            measured_cost_minor_units=measured_minor,
            currency=(
                inputs.authority_scope.currency if measured_minor is not None else None
            ),
            completed_at=after_call_at,
            reason_codes=reason_codes,
        )


__all__ = [
    "DurableDispatchInputs",
    "DurableExecutionError",
    "DurableExecutorRuntime",
    "FixtureExecutorPort",
    "RESULT_VERSION",
    "VerifiedExecutorInput",
    "VerifiedExecutorInputBundle",
]
