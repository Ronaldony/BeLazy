from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from video_factory import CORE_CONTRACT_VERSION, __version__
from video_factory.approvals import (
    ApprovalEvidence,
    ApprovalState,
    GateContext,
    build_approval_requirement,
)
from video_factory.config import (
    CONFIG_CONTRACT_VERSION,
    ConfigLayer,
    ConfigSource,
    DeterministicConfigMerger,
    EffectiveScope,
    EffectiveVersions,
    canonical_sha256,
)
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
from video_factory.engine import ExecutionMode, ExecutionModeLimits
from video_factory.providers import (
    AdapterBinding,
    AdapterContractError,
    AdapterKind,
    AllowedOutput,
    CapabilityConstraintProfile,
    CapabilityDescriptor,
    CostMeasurement,
    ExecutorAdapter,
    ExecutorDispatchContext,
    ExternalReference,
    ExternalStateUncertain,
    FirstFrameAspectBehavior,
    HumanHandoff,
    InMemoryCapabilityRegistry,
    ModeEnforcementError,
    NormalizedEvent,
    OrchestrationGuard,
    OrchestrationPolicy,
    Outcome,
    ProviderAdapter,
    ProviderPlan,
    ReadOnlyStaging,
    RegistryError,
    RequestEnvelope,
    ResultEnvelope,
    SideEffect,
    UncertaintyEvidence,
    UncertaintyModel,
    ValidationReport,
    enforce_adapter_dispatch,
    request_envelope_sha256,
    settled_uncertainty,
    unknown_cost,
    validate_descriptor,
)


INPUT_VERSION = ArtifactVersion("input/1.0")
OUTPUT_VERSION = ArtifactVersion("output/1.0")
MEDIA_CAPABILITY = CapabilityId("media.video.generate")
TASK_CAPABILITY = CapabilityId("artifact.review")


def _gate_context() -> GateContext:
    return GateContext(
        workflow_definition_sha256=HashDigest("1" * 64),
        policy_bundle_sha256=HashDigest("2" * 64),
        rules_bundle_sha256=HashDigest("3" * 64),
        effective_config_sha256=HashDigest("b" * 64),
        current_manifest_sha256=HashDigest("4" * 64),
        evidence_graph_sha256=HashDigest("5" * 64),
        executable_plan_sha256=HashDigest("6" * 64),
    )


def _approval_evidence(context: GateContext, *, expires_at: str = "2026-07-21T02:00:00Z") -> ApprovalEvidence:
    requirement = build_approval_requirement(
        str(MEDIA_CAPABILITY),
        [_artifact()],
        "b" * 64,
        capability_id=str(MEDIA_CAPABILITY),
        gate_context=context,
    )
    return ApprovalEvidence(
        evidence_id=OpaqueId("evidence-adapter"),
        requirement=requirement,
        state=ApprovalState.GRANTED,
        approver_role=RoleId("human-operator"),
        created_at="2026-07-21T00:00:00Z",
        record_sha256=HashDigest("c" * 64),
        expires_at=expires_at,
    )


def _artifact(path: str = "artifacts/input.json") -> ArtifactReference:
    return ArtifactReference(
        RelativeArtifactPath(path),
        HashDigest("a" * 64),
        INPUT_VERSION,
    )


def _request(
    capability: CapabilityId,
    mode: ExecutionMode,
    *,
    key: str = "request-key-a",
) -> RequestEnvelope:
    return RequestEnvelope(
        request_id=RequestId(f"request-{key}"),
        capability_id=capability,
        effective_execution_mode=mode,
        effective_config_sha256=HashDigest("b" * 64),
        input_artifacts=(_artifact(),),
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


class SyntheticProvider(ProviderAdapter):
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
            estimated_cost=Decimal("2"),
            reservation_reference=OpaqueId("reservation-a"),
            expected_outputs=(RelativeArtifactPath("artifacts/results/result-a.bin"),),
        )

    def derive_preview(self, request, validation):
        return {"valid": validation.accepted, "candidate_count": 1}

    def build_human_handoff(self, request, plan) -> HumanHandoff:
        return HumanHandoff(
            steps=("Open the approved interface.", "Save the result under the expected name."),
            expected_outputs=plan.expected_outputs,
        )

    def ingest(self, request, outputs, measured_cost, provenance) -> ResultEnvelope:
        return ResultEnvelope(
            request.request_id,
            Outcome.SUCCEEDED,
            tuple(outputs),
            provenance.get("external_reference") if provenance else None,
            measured_cost,
            settled_uncertainty(),
        )

    def unsafe_external_probe(self) -> None:
        self.external_calls += 1


class SyntheticExecutor(ExecutorAdapter):
    def __init__(self, *, timeout: bool = False) -> None:
        super().__init__()
        self.timeout = timeout
        self.external_calls = 0
        self.normalized_events = 0
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
        if self.timeout:
            raise ExternalStateUncertain(
                "transport timeout",
                ExternalReference("external-request-a", "external-session-a"),
            )
        return ({"event_type": "completed", "value": 1},)

    def normalize_event(self, event) -> NormalizedEvent:
        self.normalized_events += 1
        return NormalizedEvent(OpaqueId(str(event["event_type"])), {"value": event["value"]})

    def result_from_events(self, request, events) -> ResultEnvelope:
        assert events
        return ResultEnvelope(
            request.request_id,
            Outcome.SUCCEEDED,
            (),
            ExternalReference("external-request-a", "external-session-a"),
            CostMeasurement(Decimal("1"), "unit-b", False),
            settled_uncertainty(),
        )

    def _reconcile_external(self, request, external_reference) -> ResultEnvelope:
        self.timeout = False
        return ResultEnvelope(
            request.request_id,
            Outcome.SUCCEEDED,
            (),
            external_reference,
            unknown_cost(),
            settled_uncertainty(),
        )


def _executor_context(request: RequestEnvelope) -> ExecutorDispatchContext:
    return ExecutorDispatchContext(
        staging=ReadOnlyStaging(request.input_artifacts, request.allowed_outputs, True),
        requested_tools=frozenset({OpaqueId("tool-a")}),
        allowed_tools=frozenset({OpaqueId("tool-a")}),
        capability_allowlist=frozenset({request.capability_id}),
    )


_DISPATCH_TIME = datetime(2026, 7, 21, 1, 0, tzinfo=timezone.utc)


def _executor_authorization(request: RequestEnvelope):
    authorization = OrchestrationGuard().authorize(
        request,
        OrchestrationPolicy(
            request.capability_id,
            AdapterKind.EXECUTOR,
            ExecutionMode.AUTOMATED,
            False,
        ),
        current_context=_gate_context(),
        evaluated_at=_DISPATCH_TIME,
    )
    assert authorization.request_envelope_sha256 == request_envelope_sha256(request)
    return authorization


def _authorized_dispatch(
    adapter: SyntheticExecutor,
    request: RequestEnvelope,
):
    return adapter.dispatch(
        request,
        _executor_context(request),
        authorization=_executor_authorization(request),
        current_context=_gate_context(),
        evaluated_at=_DISPATCH_TIME,
    )


def _authorized_reconcile(
    adapter: SyntheticExecutor,
    request: RequestEnvelope,
):
    return adapter.reconcile(
        request,
        authorization=_executor_authorization(request),
        current_context=_gate_context(),
        evaluated_at=_DISPATCH_TIME,
    )


def _effective_mode_snapshot(
    requested: str,
    limits: ExecutionModeLimits,
):
    values = {"settings": {"execution": {"mode": requested}}, "extensions": {}}
    source = ConfigSource(
        ConfigLayer.CHANNEL,
        OpaqueId("channel-a"),
        RelativeArtifactPath("config/channel-a.json"),
        canonical_sha256(values),
        values,
    )
    return DeterministicConfigMerger().merge(
        [source],
        scope=EffectiveScope(
            OpaqueId("workspace-a"), OpaqueId("channel-a"), None, None
        ),
        versions=EffectiveVersions(
            __version__,
            CORE_CONTRACT_VERSION,
            "rules-a",
            "policy-a/1.0",
            CONFIG_CONTRACT_VERSION,
        ),
        core_lock_sha256=HashDigest("c" * 64),
        created_at="2026-01-02T03:04:05Z",
        runtime_override_allowlist=set(),
        extension_validators={},
        execution_mode_limits=limits,
    )


def test_provider_human_only_returns_awaiting_without_external_call() -> None:
    adapter = SyntheticProvider()
    response = adapter.human_only(_request(MEDIA_CAPABILITY, ExecutionMode.HUMAN_ONLY))
    assert response.result.outcome is Outcome.AWAITING_HUMAN
    assert response.handoff is not None
    assert response.plan is not None
    assert response.handoff.expected_outputs == response.plan.expected_outputs
    assert adapter.external_calls == 0


def test_executor_timeout_is_external_uncertain_and_blocks_retry_until_reconcile() -> None:
    adapter = SyntheticExecutor(timeout=True)
    request = _request(TASK_CAPABILITY, ExecutionMode.AUTOMATED)
    result = _authorized_dispatch(adapter, request)
    assert result.outcome is Outcome.EXTERNAL_UNCERTAIN
    assert result.outcome is not Outcome.FAILED
    assert result.external_reference == ExternalReference(
        "external-request-a", "external-session-a"
    )
    assert result.uncertainty.uncertain is True
    with pytest.raises(AdapterContractError, match="reconcile"):
        _authorized_dispatch(adapter, request)
    assert adapter.external_calls == 1
    with pytest.raises(ModeEnforcementError, match="authorization"):
        adapter.reconcile(request)
    assert adapter.observe(request).outcome is Outcome.EXTERNAL_UNCERTAIN

    different_request = replace(
        request, request_id=RequestId("request-different-reconcile")
    )
    with pytest.raises(AdapterContractError, match="another request"):
        adapter.reconcile(
            different_request,
            authorization=_executor_authorization(different_request),
            current_context=_gate_context(),
            evaluated_at=_DISPATCH_TIME,
        )
    with pytest.raises(AdapterContractError, match="reference does not match"):
        adapter.reconcile(
            request,
            ExternalReference("different-external-request", "different-session"),
            authorization=_executor_authorization(request),
            current_context=_gate_context(),
            evaluated_at=_DISPATCH_TIME,
        )
    reconciled = _authorized_reconcile(adapter, request)
    assert reconciled.outcome is Outcome.SUCCEEDED


def test_config_merge_is_mode_enforcement_point_one_and_uses_most_restrictive_limit() -> None:
    snapshot = _effective_mode_snapshot(
        "automated",
        ExecutionModeLimits(
            channel_maximum=ExecutionMode.HUMAN_ONLY,
            adapter_maximum=ExecutionMode.AUTOMATED,
        ),
    )
    assert snapshot.effective["settings"]["execution"]["mode"] == "human_only"
    assert "execution_mode:effective=human_only" in snapshot.constraints.applied

    unknown = _effective_mode_snapshot(
        "unknown-mode",
        ExecutionModeLimits(channel_maximum=None, adapter_maximum=None),
    )
    assert unknown.effective["settings"]["execution"]["mode"] == "human_only"
    assert {
        "execution_mode:fail_safe=channel",
        "execution_mode:fail_safe=mode",
        "execution_mode:fail_safe=adapter",
    } <= set(unknown.constraints.applied)


def test_orchestrator_is_mode_enforcement_point_two_before_evidence_or_selection() -> None:
    request = _request(MEDIA_CAPABILITY, ExecutionMode.AUTOMATED)
    policy = OrchestrationPolicy(
        MEDIA_CAPABILITY,
        AdapterKind.PROVIDER,
        ExecutionMode.AUTOMATED,
        True,
    )
    with pytest.raises(ModeEnforcementError, match="automation"):
        OrchestrationGuard().authorize(request, policy)

    human_request = _request(MEDIA_CAPABILITY, ExecutionMode.HUMAN_ONLY, key="human-a")
    human_policy = OrchestrationPolicy(
        MEDIA_CAPABILITY,
        AdapterKind.PROVIDER,
        ExecutionMode.HUMAN_ONLY,
        True,
    )
    with pytest.raises(ModeEnforcementError, match="evidence"):
        OrchestrationGuard().authorize(human_request, human_policy)


def test_orchestration_guard_binds_evidence_to_current_context_and_expiry() -> None:
    request = _request(MEDIA_CAPABILITY, ExecutionMode.HUMAN_ONLY, key="human-bound")
    policy = OrchestrationPolicy(
        MEDIA_CAPABILITY,
        AdapterKind.PROVIDER,
        ExecutionMode.HUMAN_ONLY,
        True,
    )
    context = _gate_context()
    evaluation = datetime(2026, 7, 21, 1, 0, tzinfo=timezone.utc)
    authorization = OrchestrationGuard().authorize(
        request,
        policy,
        _approval_evidence(context),
        current_context=context,
        evaluated_at=evaluation,
    )
    assert authorization.human_evidence_id == "evidence-adapter"
    assert authorization.gate_context_sha256 is not None

    with pytest.raises(ModeEnforcementError, match="expired"):
        OrchestrationGuard().authorize(
            request,
            policy,
            _approval_evidence(context, expires_at="2026-07-21T01:00:00Z"),
            current_context=context,
            evaluated_at=evaluation,
        )


@pytest.mark.parametrize(
    "field",
    [
        "workflow_definition_sha256",
        "policy_bundle_sha256",
        "rules_bundle_sha256",
        "effective_config_sha256",
        "current_manifest_sha256",
        "evidence_graph_sha256",
        "executable_plan_sha256",
    ],
)
def test_orchestration_guard_rejects_every_stale_context_digest(field: str) -> None:
    request = _request(MEDIA_CAPABILITY, ExecutionMode.HUMAN_ONLY, key=f"stale-{field}")
    policy = OrchestrationPolicy(
        MEDIA_CAPABILITY,
        AdapterKind.PROVIDER,
        ExecutionMode.HUMAN_ONLY,
        True,
    )
    current = _gate_context()
    stale = replace(current, **{field: HashDigest("e" * 64)})
    evidence_context = stale
    if field == "effective_config_sha256":
        # Keep the evidence requirement internally consistent while the current
        # request remains bound to the real effective config.
        requirement = build_approval_requirement(
            str(MEDIA_CAPABILITY),
            [_artifact()],
            "e" * 64,
            capability_id=str(MEDIA_CAPABILITY),
            gate_context=stale,
        )
        evidence = ApprovalEvidence(
            evidence_id=OpaqueId("evidence-stale-config"),
            requirement=requirement,
            state=ApprovalState.GRANTED,
            approver_role=RoleId("human-operator"),
            created_at="2026-07-21T00:00:00Z",
            record_sha256=HashDigest("c" * 64),
            expires_at="2026-07-21T02:00:00Z",
        )
    else:
        evidence = _approval_evidence(evidence_context)
    with pytest.raises(ModeEnforcementError):
        OrchestrationGuard().authorize(
            request,
            policy,
            evidence,
            current_context=current,
            evaluated_at=datetime(2026, 7, 21, 1, 0, tzinfo=timezone.utc),
        )


def test_registry_is_mode_enforcement_point_three_for_capability_and_kind() -> None:
    provider = SyntheticProvider()
    executor = SyntheticExecutor()
    registry = InMemoryCapabilityRegistry(
        (
            AdapterBinding(MEDIA_CAPABILITY, OpaqueId("adapter-a"), AdapterKind.PROVIDER),
            AdapterBinding(TASK_CAPABILITY, OpaqueId("adapter-b"), AdapterKind.EXECUTOR),
        ),
        (provider, executor),
    )
    assert registry.resolve(MEDIA_CAPABILITY, AdapterKind.PROVIDER) is provider
    assert registry.resolve(TASK_CAPABILITY, AdapterKind.EXECUTOR) is executor

    wrong_kind = InMemoryCapabilityRegistry(
        (AdapterBinding(TASK_CAPABILITY, OpaqueId("adapter-b"), AdapterKind.PROVIDER),),
        (executor,),
    )
    with pytest.raises(RegistryError, match="kind"):
        wrong_kind.resolve(TASK_CAPABILITY, AdapterKind.PROVIDER)


def test_adapter_boundary_is_mode_enforcement_point_four() -> None:
    provider = SyntheticProvider()
    human_request = _request(MEDIA_CAPABILITY, ExecutionMode.HUMAN_ONLY)
    with pytest.raises(ModeEnforcementError, match="dispatch"):
        enforce_adapter_dispatch(human_request, provider.descriptor, AdapterKind.PROVIDER)
    assert provider.external_calls == 0

    executor = SyntheticExecutor()
    non_automated = _request(TASK_CAPABILITY, ExecutionMode.HUMAN_ONLY, key="task-human")
    with pytest.raises((AdapterContractError, ModeEnforcementError)):
        executor.dispatch(non_automated, _executor_context(non_automated))
    assert executor.external_calls == 0


def test_media_provider_descriptor_cannot_enable_automated_mode() -> None:
    descriptor = CapabilityDescriptor(
        adapter_id=OpaqueId("adapter-c"),
        adapter_kind=AdapterKind.PROVIDER,
        contract_version="1.0",
        capabilities=frozenset({MEDIA_CAPABILITY}),
        supported_execution_modes=frozenset({ExecutionMode.AUTOMATED}),
        input_artifact_versions=frozenset({INPUT_VERSION}),
        output_artifact_versions=frozenset({OUTPUT_VERSION}),
        side_effects=frozenset({SideEffect.EXTERNAL_CALL}),
        uncertainty_model=UncertaintyModel(True, True),
    )
    with pytest.raises(AdapterContractError, match="cannot enable automated"):
        validate_descriptor(descriptor)


def test_descriptor_validates_injected_generation_constraints() -> None:
    profile = CapabilityConstraintProfile(
        profile_id=OpaqueId("profile-media"),
        profile_sha256=HashDigest("c" * 64),
        capability_id=MEDIA_CAPABILITY,
        minimum_duration_seconds=Decimal("4"),
        first_frame_aspect_behavior=FirstFrameAspectBehavior.MATCH_OUTPUT,
        unsupported_render_dependencies=frozenset({OpaqueId("screen-text")}),
    )
    descriptor = CapabilityDescriptor(
        adapter_id=OpaqueId("adapter-profiled"),
        adapter_kind=AdapterKind.PROVIDER,
        contract_version="1.0",
        capabilities=frozenset({MEDIA_CAPABILITY}),
        supported_execution_modes=frozenset({ExecutionMode.HUMAN_ONLY}),
        input_artifact_versions=frozenset({INPUT_VERSION}),
        output_artifact_versions=frozenset({OUTPUT_VERSION}),
        side_effects=frozenset({SideEffect.EXTERNAL_CALL}),
        uncertainty_model=UncertaintyModel(False, True),
        constraint_profiles=(profile,),
    )
    validate_descriptor(descriptor)

    invalid = CapabilityDescriptor(
        adapter_id=descriptor.adapter_id,
        adapter_kind=descriptor.adapter_kind,
        contract_version=descriptor.contract_version,
        capabilities=descriptor.capabilities,
        supported_execution_modes=descriptor.supported_execution_modes,
        input_artifact_versions=descriptor.input_artifact_versions,
        output_artifact_versions=descriptor.output_artifact_versions,
        side_effects=descriptor.side_effects,
        uncertainty_model=descriptor.uncertainty_model,
        constraint_profiles=(
            CapabilityConstraintProfile(
                profile_id=OpaqueId("profile-bad"),
                profile_sha256=HashDigest("c" * 64),
                capability_id=CapabilityId("other-capability"),
                minimum_duration_seconds=Decimal("4"),
                first_frame_aspect_behavior=FirstFrameAspectBehavior.MATCH_OUTPUT,
                unsupported_render_dependencies=frozenset(),
            ),
        ),
    )
    with pytest.raises(AdapterContractError, match="declared"):
        validate_descriptor(invalid)


def test_executor_side_effect_boundary_rejects_missing_or_mismatched_authorization() -> None:
    exposed: set[str] = set()

    raw_request = _request(MEDIA_CAPABILITY, ExecutionMode.AUTOMATED, key="raw-mode")
    if raw_request.effective_execution_mode is ExecutionMode.AUTOMATED:
        exposed.add("effective-config")

    executor = SyntheticExecutor()
    registry = InMemoryCapabilityRegistry(
        (AdapterBinding(TASK_CAPABILITY, OpaqueId("adapter-b"), AdapterKind.EXECUTOR),),
        (executor,),
    )
    selected_without_orchestration = registry.resolve(TASK_CAPABILITY, AdapterKind.EXECUTOR)
    if selected_without_orchestration is executor:
        exposed.add("orchestrator")

    request = _request(TASK_CAPABILITY, ExecutionMode.AUTOMATED, key="direct-binding")
    with pytest.raises(ModeEnforcementError, match="authorization"):
        executor.dispatch(request, _executor_context(request))
    assert executor.external_calls == 0

    other_request = _request(
        TASK_CAPABILITY, ExecutionMode.AUTOMATED, key="other-binding"
    )
    with pytest.raises(ModeEnforcementError, match="another request"):
        executor.dispatch(
            request,
            _executor_context(request),
            authorization=_executor_authorization(other_request),
            current_context=_gate_context(),
            evaluated_at=_DISPATCH_TIME,
        )
    assert executor.external_calls == 0

    stale_context = replace(
        _gate_context(), policy_bundle_sha256=HashDigest("e" * 64)
    )
    with pytest.raises(ModeEnforcementError, match="another gate context"):
        executor.dispatch(
            request,
            _executor_context(request),
            authorization=_executor_authorization(request),
            current_context=stale_context,
            evaluated_at=_DISPATCH_TIME,
        )
    assert executor.external_calls == 0

    result = _authorized_dispatch(executor, request)
    assert result.outcome is Outcome.SUCCEEDED

    human_request = _request(TASK_CAPABILITY, ExecutionMode.HUMAN_ONLY, key="direct-process")
    tuple(executor._dispatch_stream(human_request, _executor_context(human_request)))
    if executor.external_calls == 2:
        exposed.add("adapter-boundary")

    assert exposed == {
        "effective-config",
        "orchestrator",
        "adapter-boundary",
    }


def test_executor_rechecks_authorization_time_before_external_call() -> None:
    executor = SyntheticExecutor()
    request = _request(TASK_CAPABILITY, ExecutionMode.AUTOMATED, key="time-binding")
    authorization = _executor_authorization(request)

    future = replace(authorization, evaluated_at="2026-07-21T02:00:00Z")
    with pytest.raises(ModeEnforcementError, match="not yet valid"):
        executor.dispatch(
            request,
            _executor_context(request),
            authorization=future,
            current_context=_gate_context(),
            evaluated_at=_DISPATCH_TIME,
        )
    assert executor.external_calls == 0

    expired = replace(
        authorization,
        human_evidence_id="evidence-expired",
        valid_until="2026-07-21T01:00:00Z",
    )
    with pytest.raises(ModeEnforcementError, match="expired"):
        executor.dispatch(
            request,
            _executor_context(request),
            authorization=expired,
            current_context=_gate_context(),
            evaluated_at=_DISPATCH_TIME,
        )
    assert executor.external_calls == 0


@pytest.mark.parametrize(
    "mutate",
    [
        lambda request: replace(
            request, input_artifacts=(_artifact("artifacts/unapproved.json"),)
        ),
        lambda request: replace(
            request,
            allowed_outputs=(
                AllowedOutput(
                    RelativeArtifactPath("artifacts/other-results"),
                    frozenset({OUTPUT_VERSION}),
                ),
            ),
        ),
        lambda request: replace(
            request, idempotency_key=IdempotencyKey("changed-idempotency")
        ),
        lambda request: replace(request, creator_role=RoleId("role-other-creator")),
        lambda request: replace(request, reviewer_role=RoleId("role-other-reviewer")),
    ],
    ids=("inputs", "outputs", "idempotency", "creator", "reviewer"),
)
def test_executor_authorization_binds_the_full_request_envelope(mutate) -> None:
    executor = SyntheticExecutor()
    original = _request(
        TASK_CAPABILITY, ExecutionMode.AUTOMATED, key="exact-request"
    )
    authorization = _executor_authorization(original)
    changed = mutate(original)
    assert changed.request_id == original.request_id
    with pytest.raises(ModeEnforcementError, match="exact request envelope"):
        executor.dispatch(
            changed,
            _executor_context(changed),
            authorization=authorization,
            current_context=_gate_context(),
            evaluated_at=_DISPATCH_TIME,
        )
    assert executor.external_calls == 0

def test_no_literal_opaque_adapter_id_comparison_exists_in_core_source() -> None:
    root = Path(__file__).resolve().parents[2] / "src"
    findings: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            operands = (node.left, *node.comparators)
            has_identity = any(
                (isinstance(item, ast.Name) and item.id == "adapter_id")
                or (isinstance(item, ast.Attribute) and item.attr == "adapter_id")
                for item in operands
            )
            has_literal = any(
                isinstance(item, ast.Constant) and isinstance(item.value, str)
                for item in operands
            )
            if has_identity and has_literal:
                findings.append(f"{path.relative_to(root)}:{node.lineno}")
    assert findings == []
