"""Synthetic ProviderAdapter / ExecutorAdapter compatibility path.

Concrete product names are forbidden. Adapters are named ``adapter-a`` and
``adapter-b`` only. The provider human_only path must end in AWAITING_HUMAN
without any external call.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from video_factory.approvals import gate_context_sha256
from video_factory.authority import (
    AssuranceProfile,
    AuthorityDecisionStatus,
    AuthorityScope,
    AuthoritySource,
    AuthorityVerificationReceipt,
    AutonomyProfile,
    LedgerRecordState,
    OutputScope,
    ProfileSelection,
    VerificationPurpose,
    authority_verification_receipt_sha256,
    build_action_authority_request,
    evaluate_authority,
    target_policy_bundle,
)
from video_factory.config import canonical_sha256
from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    CapabilityId,
    HashDigest,
    IdempotencyKey,
    OpaqueId,
    RelativeArtifactPath,
    RequestId,
    RoleId,
)
from video_factory.engine import ExecutionMode
from video_factory.mutation import (
    WorkspaceObservation,
    WorkspaceRevision,
    WorkspaceRevisionOrigin,
    WorkspaceTrustState,
    workspace_revision_to_mapping,
)
from video_factory.providers import (
    AdapterBinding,
    AdapterKind,
    AllowedOutput,
    CapabilityDescriptor,
    ExecutorAdapter,
    ExecutorDispatchContext,
    ExternalReference,
    HumanHandoff,
    InMemoryCapabilityRegistry,
    NormalizedEvent,
    OrchestrationGuard,
    OrchestrationPolicy,
    Outcome,
    ProviderAdapter,
    ProviderPlan,
    ReadOnlyStaging,
    RequestEnvelope,
    ResultEnvelope,
    SideEffect,
    UncertaintyModel,
    ValidationReport,
    settled_uncertainty,
    unknown_cost,
    request_envelope_sha256,
    validate_descriptor,
)
from video_factory.workflow import (
    GateStatus,
    MaterialContextSeed,
    build_executable_production_plan,
    build_gate_result,
    default_workflow_definition,
    evaluate_workflow,
)

INPUT_VERSION = ArtifactVersion("input/1.0")
OUTPUT_VERSION = ArtifactVersion("output/1.0")
MEDIA_CAPABILITY = CapabilityId("media.video.generate")
TASK_CAPABILITY = CapabilityId("paid_external_generation")


def _workflow_plan():
    definition = default_workflow_definition()
    policy = target_policy_bundle()
    action = next(
        item for item in definition.actions if item.action_id == "run_external_generation"
    )
    earlier_claims = {
        item.satisfies_claim_id
        for item in definition.actions
        if item.priority < action.priority
        and item.satisfies_claim_id != action.satisfies_claim_id
    }
    results = []
    for claim in definition.claims:
        if claim.claim_id in earlier_claims:
            results.append(
                build_gate_result(
                    str(claim.gate_id),
                    GateStatus.PASS,
                    evidence_sha256s=("a" * 64,),
                )
            )
        elif claim.claim_id == action.satisfies_claim_id:
            results.append(
                build_gate_result(
                    str(claim.gate_id),
                    GateStatus.BLOCKED,
                    reason_codes=(action.trigger_reason_codes[0],),
                    messages=("blocked",),
                )
            )
        else:
            results.append(
                build_gate_result(
                    str(claim.gate_id),
                    GateStatus.UNKNOWN,
                    reason_codes=("workflow.gate.missing",),
                    messages=("missing",),
                )
            )
    evaluation = evaluate_workflow(
        definition,
        results,
        MaterialContextSeed(
            workflow_definition_sha256=definition.definition_sha256,
            policy_bundle_sha256=policy.bundle_sha256,
            rules_bundle_sha256=HashDigest("3" * 64),
            effective_config_sha256=HashDigest("b" * 64),
            current_manifest_sha256=HashDigest("4" * 64),
            evidence_graph_sha256=HashDigest("5" * 64),
        ),
    )
    assert evaluation.recommended_action_id == action.action_id
    return build_executable_production_plan(
        definition,
        evaluation,
        str(action.action_id),
    )


class AdapterA(ProviderAdapter):
    """Synthetic media provider — no network, no product identity."""

    def __init__(self) -> None:
        self.external_calls = 0
        self._descriptor = CapabilityDescriptor(
            adapter_id=OpaqueId("adapter-a"),
            adapter_kind=AdapterKind.PROVIDER,
            contract_version="1.0",
            capabilities=frozenset({MEDIA_CAPABILITY}),
            supported_execution_modes=frozenset(
                {ExecutionMode.PREVIEW_ONLY, ExecutionMode.HUMAN_ONLY}
            ),
            input_artifact_versions=frozenset({INPUT_VERSION}),
            output_artifact_versions=frozenset({OUTPUT_VERSION}),
            side_effects=frozenset(
                {
                    SideEffect.EXTERNAL_CALL,
                    SideEffect.QUOTA_CONSUMING,
                    SideEffect.DATA_UPLOAD,
                }
            ),
            uncertainty_model=UncertaintyModel(False, True),
        )

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return self._descriptor

    def validate(self, request, reference_media) -> ValidationReport:
        return ValidationReport(True, (), {"reference_count": len(reference_media)})

    def estimate(self, request) -> ProviderPlan:
        return ProviderPlan(
            request_id=request.request_id,
            candidate_count=1,
            cost_unit="unit-a",
            estimated_cost=Decimal("1"),
            reservation_reference=OpaqueId("reservation-a"),
            expected_outputs=(RelativeArtifactPath("artifacts/results/result-a.bin"),),
        )

    def derive_preview(self, request, validation):
        return {"valid": validation.accepted}

    def build_human_handoff(self, request, plan) -> HumanHandoff:
        return HumanHandoff(
            steps=("Prepare the approved interface.", "Save under the expected name."),
            expected_outputs=plan.expected_outputs,
        )

    def ingest(self, request, outputs, measured_cost, provenance) -> ResultEnvelope:
        self.external_calls += 1
        return ResultEnvelope(
            request.request_id,
            Outcome.SUCCEEDED,
            tuple(outputs),
            None,
            measured_cost,
            settled_uncertainty(),
        )


class AdapterB(ExecutorAdapter):
    """Synthetic structured-task executor — in-memory stream only."""

    def __init__(self) -> None:
        super().__init__()
        self.external_calls = 0
        self._descriptor = CapabilityDescriptor(
            adapter_id=OpaqueId("adapter-b"),
            adapter_kind=AdapterKind.EXECUTOR,
            contract_version="1.0",
            capabilities=frozenset({TASK_CAPABILITY}),
            supported_execution_modes=frozenset({ExecutionMode.AUTOMATED}),
            input_artifact_versions=frozenset({INPUT_VERSION}),
            output_artifact_versions=frozenset({OUTPUT_VERSION}),
            side_effects=frozenset({SideEffect.EXTERNAL_CALL, SideEffect.LOCAL_WRITE}),
            uncertainty_model=UncertaintyModel(True, True),
        )

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return self._descriptor

    def _dispatch_stream(self, request, context):
        self.external_calls += 1
        return ({"event_type": "completed", "value": 1},)

    def normalize_event(self, event) -> NormalizedEvent:
        return NormalizedEvent(OpaqueId(str(event["event_type"])), {"value": event["value"]})

    def result_from_events(self, request, events) -> ResultEnvelope:
        return ResultEnvelope(
            request.request_id,
            Outcome.SUCCEEDED,
            (),
            ExternalReference("external-request-a", "external-session-a"),
            unknown_cost(),
            settled_uncertainty(),
        )

    def _reconcile_external(self, request, external_reference) -> ResultEnvelope:
        return ResultEnvelope(
            request.request_id,
            Outcome.SUCCEEDED,
            (),
            external_reference,
            unknown_cost(),
            settled_uncertainty(),
        )


def _request(capability: CapabilityId, mode: ExecutionMode, *, key: str) -> RequestEnvelope:
    return RequestEnvelope(
        request_id=RequestId(f"request-{key}"),
        capability_id=capability,
        effective_execution_mode=mode,
        effective_config_sha256=HashDigest("b" * 64),
        input_artifacts=(
            ArtifactReference(
                RelativeArtifactPath("artifacts/input.json"),
                HashDigest("a" * 64),
                INPUT_VERSION,
            ),
        ),
        allowed_outputs=(
            AllowedOutput(
                RelativeArtifactPath("artifacts/results"),
                frozenset({OUTPUT_VERSION}),
            ),
        ),
        idempotency_key=IdempotencyKey(key),
        creator_role=RoleId("role-creator"),
        reviewer_role=RoleId("role-reviewer"),
    )


def _authority_reference(path: str, marker: str, version: str) -> ArtifactReference:
    return ArtifactReference(
        RelativeArtifactPath(path),
        HashDigest(marker * 64),
        ArtifactVersion(version),
    )


class _TrustedAuthorityLedger:
    def _receipt(
        self,
        request,
        risk_sha256,
        *,
        purpose,
        evaluated_at,
        decision_sha256=None,
        adapter_id=None,
        service_identity=None,
        workspace_observation_sha256=None,
    ) -> AuthorityVerificationReceipt:
        provisional = AuthorityVerificationReceipt(
            artifact_version="authority-verification-receipt/1.0",
            receipt_id=OpaqueId("pending"),
            receipt_sha256=HashDigest("0" * 64),
            purpose=purpose,
            action_request_sha256=request.request_sha256,
            authority_decision_sha256=decision_sha256,
            gate_context_sha256=gate_context_sha256(request.gate_context),
            risk_assessment_sha256=risk_sha256,
            authority_source=AuthoritySource.ONE_SHOT_HUMAN,
            ledger_state=LedgerRecordState.ACTIVE,
            ledger_head_sha256=HashDigest("7" * 64),
            ledger_entry=_authority_reference(
                "authority/ledger-entry.json",
                "8",
                "authority-ledger-entry/1.0",
            ),
            grant_sha256=None,
            principal_ids=(OpaqueId("human-a"),),
            signature_verification_refs=(
                _authority_reference(
                    "authority/signature.json",
                    "9",
                    "signature-verification/1.0",
                ),
            ),
            revocation_checked_at=evaluated_at.isoformat(),
            kill_switch_clear=True,
            reserved_cost_minor_units=request.scope.cost_minor_units,
            currency=request.scope.currency,
            reserved_candidates=request.scope.candidate_count,
            retry_index=request.scope.retry_index,
            idempotency_key=request.idempotency_key,
            workspace_id=request.scope.workspace_id,
            workspace_observation_sha256=(
                HashDigest(str(workspace_observation_sha256))
                if workspace_observation_sha256 is not None
                else None
            ),
            adapter_id=OpaqueId(str(adapter_id)) if adapter_id is not None else None,
            service_identity=(
                OpaqueId(str(service_identity))
                if service_identity is not None
                else None
            ),
            evaluated_at=evaluated_at.isoformat(),
            valid_until=(evaluated_at + timedelta(minutes=5)).isoformat(),
        )
        digest = authority_verification_receipt_sha256(provisional)
        return replace(
            provisional,
            receipt_id=OpaqueId(f"authority-receipt-{str(digest)[:20]}"),
            receipt_sha256=digest,
        )

    def verify_current(
        self,
        request,
        risk,
        presented_grant,
        authority_references,
        *,
        current_context,
        evaluated_at,
    ):
        return self._receipt(
            request,
            risk.assessment_sha256,
            purpose=VerificationPurpose.INITIAL_DECISION,
            evaluated_at=evaluated_at,
        )

    def revalidate_and_reserve_current(
        self,
        decision,
        request,
        *,
        current_context,
        workspace_observation_sha256,
        adapter_id,
        service_identity,
        evaluated_at,
        purpose,
    ):
        return self._receipt(
            request,
            decision.risk_assessment_sha256,
            purpose=purpose,
            evaluated_at=evaluated_at,
            decision_sha256=decision.decision_sha256,
            adapter_id=adapter_id,
            service_identity=service_identity,
            workspace_observation_sha256=workspace_observation_sha256,
        )


def _w04_authority(request: RequestEnvelope, evaluated_at: datetime):
    plan = _workflow_plan()
    assert plan.capability_id == request.capability_id
    authority_request = build_action_authority_request(
        request_id=str(request.request_id),
        request_envelope_sha256=request_envelope_sha256(request),
        idempotency_key=str(request.idempotency_key),
        plan=plan,
        profiles=ProfileSelection(
            AssuranceProfile.PRODUCTION,
            AutonomyProfile.ASSISTED,
        ),
        scope=AuthorityScope(
            workspace_id=OpaqueId("workspace-compat"),
            channel_id=OpaqueId("channel-compat"),
            concept_id=OpaqueId("concept-compat"),
            episode_id=OpaqueId("episode-compat"),
            provider_id=OpaqueId("adapter-b"),
            model_id=OpaqueId("model-b"),
            destination="destination-b",
            cost_minor_units=1,
            currency="USD",
            candidate_count=1,
            retry_index=0,
            input_artifacts=request.input_artifacts,
            allowed_outputs=tuple(
                OutputScope(
                    str(item.path_prefix),
                    tuple(sorted(str(value) for value in item.artifact_versions)),
                )
                for item in request.allowed_outputs
            ),
        ),
    )
    ledger = _TrustedAuthorityLedger()
    _, decision = evaluate_authority(
        authority_request,
        target_policy_bundle(),
        ledger=ledger,
        authority_references=(
            _authority_reference(
                "authority/human.json",
                "6",
                "human-approval/1.0",
            ),
        ),
        evaluated_at=evaluated_at,
    )
    assert decision.status is AuthorityDecisionStatus.AUTHORIZED
    return authority_request, decision, ledger


def test_synthetic_adapters_declare_capabilities_and_pass_descriptor_validation() -> None:
    provider = AdapterA()
    executor = AdapterB()
    validate_descriptor(provider.descriptor)
    validate_descriptor(executor.descriptor)
    assert str(provider.descriptor.adapter_id) == "adapter-a"
    assert str(executor.descriptor.adapter_id) == "adapter-b"
    assert MEDIA_CAPABILITY in provider.descriptor.capabilities
    assert TASK_CAPABILITY in executor.descriptor.capabilities


def test_human_only_provider_ends_awaiting_human_without_external_call() -> None:
    provider = AdapterA()
    response = provider.human_only(
        _request(MEDIA_CAPABILITY, ExecutionMode.HUMAN_ONLY, key="compat-human")
    )
    assert response.result.outcome is Outcome.AWAITING_HUMAN
    assert response.handoff is not None
    assert response.plan is not None
    assert response.handoff.expected_outputs == response.plan.expected_outputs
    # human_only must never invoke ingest or any external probe.
    assert provider.external_calls == 0


def test_registry_resolves_synthetic_adapters_by_capability_and_kind() -> None:
    provider = AdapterA()
    executor = AdapterB()
    registry = InMemoryCapabilityRegistry(
        (
            AdapterBinding(MEDIA_CAPABILITY, OpaqueId("adapter-a"), AdapterKind.PROVIDER),
            AdapterBinding(TASK_CAPABILITY, OpaqueId("adapter-b"), AdapterKind.EXECUTOR),
        ),
        (provider, executor),
    )
    assert registry.resolve(MEDIA_CAPABILITY, AdapterKind.PROVIDER) is provider
    assert registry.resolve(TASK_CAPABILITY, AdapterKind.EXECUTOR) is executor

    request = _request(TASK_CAPABILITY, ExecutionMode.AUTOMATED, key="compat-exec")
    context = ExecutorDispatchContext(
        staging=ReadOnlyStaging(request.input_artifacts, request.allowed_outputs, True),
        requested_tools=frozenset({OpaqueId("tool-a")}),
        allowed_tools=frozenset({OpaqueId("tool-a")}),
        capability_allowlist=frozenset({request.capability_id}),
    )
    gate_context = _workflow_plan().gate_context
    evaluated_at = datetime(2026, 7, 21, 1, 0, tzinfo=timezone.utc)
    workspace_observation = WorkspaceObservation(
        workspace_id=OpaqueId("workspace-compat"),
        revision_id=OpaqueId("revision-compat"),
        manifest_sha256=HashDigest("4" * 64),
        trust_state=WorkspaceTrustState.TRUSTED,
        complete=True,
        entries=(),
    )
    workspace_revision = WorkspaceRevision(
        revision_id=OpaqueId("revision-compat"),
        workspace_id=OpaqueId("workspace-compat"),
        origin=WorkspaceRevisionOrigin.RECONCILED_BASELINE,
        parent_revision_id=None,
        reconciliation_evidence=ArtifactReference(
            RelativeArtifactPath("reconciliation/baseline.json"),
            HashDigest("9" * 64),
            ArtifactVersion("workspace-reconciliation/1.0"),
        ),
        manifest_sha256=HashDigest("4" * 64),
        created_at="2026-07-21T00:00:00Z",
        plan_id=OpaqueId("baseline-plan"),
        receipt_id=OpaqueId("baseline-receipt"),
        trust_state=WorkspaceTrustState.TRUSTED,
        entries=(),
    )
    workspace_revision_sha256 = str(
        canonical_sha256(workspace_revision_to_mapping(workspace_revision))
    )
    authorization = OrchestrationGuard().authorize(
        request,
        OrchestrationPolicy(
            TASK_CAPABILITY,
            AdapterKind.EXECUTOR,
            ExecutionMode.AUTOMATED,
            False,
        ),
        current_context=gate_context,
        evaluated_at=evaluated_at,
        workspace_observation=workspace_observation,
        expected_workspace_id="workspace-compat",
        expected_workspace_revision_id="revision-compat",
        expected_workspace_revision=workspace_revision,
        expected_workspace_revision_sha256=workspace_revision_sha256,
    )
    authority_request, authority_decision, authority_ledger = _w04_authority(
        request,
        evaluated_at,
    )
    result = executor.dispatch(
        request,
        context,
        authorization=authorization,
        current_context=gate_context,
        evaluated_at=evaluated_at,
        workspace_observation=workspace_observation,
        expected_workspace_id="workspace-compat",
        expected_workspace_revision_id="revision-compat",
        expected_workspace_revision=workspace_revision,
        expected_workspace_revision_sha256=workspace_revision_sha256,
        authority_request=authority_request,
        authority_decision=authority_decision,
        authority_ledger=authority_ledger,
        service_identity="executor-service-compat",
    )
    assert result.outcome is Outcome.SUCCEEDED
    assert executor.external_calls == 1
