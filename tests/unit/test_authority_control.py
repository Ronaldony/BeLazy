"""W04 current-ledger authority and pre-side-effect receipt boundaries."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from video_factory.approvals import GateContext, gate_context_sha256
from video_factory.artifacts import validate_artifact_mapping
from video_factory.authority import (
    ActionRisk,
    ApprovalRequest,
    AssuranceProfile,
    AuthorityContractError,
    AuthorityDecisionStatus,
    AuthorityScope,
    AuthoritySource,
    AuthorityVerificationReceipt,
    AutonomyProfile,
    LedgerRecordState,
    OutputScope,
    ProfileSelection,
    UnverifiedStandingAuthorization,
    VerificationPurpose,
    approval_request_to_mapping,
    authority_artifact_from_bytes,
    authority_artifact_to_bytes,
    authority_decision_to_mapping,
    authority_verification_receipt_sha256,
    authority_verification_receipt_to_mapping,
    build_action_authority_request,
    build_approval_request,
    build_bound_action_authority_request,
    evaluate_authority,
    revalidate_authority_for_side_effect,
    standing_authorization_sha256,
    target_policy_bundle,
    validate_standing_authorization,
)
from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    HashDigest,
    OpaqueId,
    RelativeArtifactPath,
)
from video_factory.engine import GenerationReadinessPlan
from video_factory.workflow import (
    AuthorityRequirement,
    GateStatus,
    MaterialContextSeed,
    build_executable_production_plan,
    build_gate_result,
    default_workflow_definition,
    evaluate_workflow,
)


NOW = datetime(2026, 8, 8, 0, 0, tzinfo=timezone.utc)


def _ref(path: str, digest: str = "a", version: str = "brief/1.0") -> ArtifactReference:
    return ArtifactReference(
        path=RelativeArtifactPath(path),
        sha256=HashDigest(digest * 64),
        artifact_version=ArtifactVersion(version),
    )


def _plan(action_id: str):
    definition = default_workflow_definition()
    policy = target_policy_bundle()
    action = next(item for item in definition.actions if str(item.action_id) == action_id)
    earlier_claims = {
        str(item.satisfies_claim_id)
        for item in definition.actions
        if item.priority < action.priority and item.satisfies_claim_id != action.satisfies_claim_id
    }
    results = []
    for claim in definition.claims:
        target = claim.claim_id == action.satisfies_claim_id
        if str(claim.claim_id) in earlier_claims:
            results.append(build_gate_result(str(claim.gate_id), GateStatus.PASS, evidence_sha256s=("b" * 64,)))
        elif target:
            reason = action.trigger_reason_codes[0] if action.trigger_reason_codes else f"workflow.{claim.claim_id}.blocked"
            results.append(
                build_gate_result(
                    str(claim.gate_id), GateStatus.BLOCKED,
                    reason_codes=(reason,), messages=("blocked",),
                )
            )
        else:
            results.append(
                build_gate_result(
                    str(claim.gate_id), GateStatus.UNKNOWN,
                    reason_codes=("workflow.gate.missing",), messages=("missing",),
                )
            )
    context = MaterialContextSeed(
        workflow_definition_sha256=definition.definition_sha256,
        policy_bundle_sha256=policy.bundle_sha256,
        rules_bundle_sha256=HashDigest("c" * 64),
        effective_config_sha256=HashDigest("d" * 64),
        current_manifest_sha256=HashDigest("e" * 64),
        evidence_graph_sha256=HashDigest("f" * 64),
    )
    evaluation = evaluate_workflow(definition, results, context)
    assert str(evaluation.recommended_action_id) == action_id
    return build_executable_production_plan(definition, evaluation, action_id)


def _scope(*, inputs: tuple[ArtifactReference, ...] = ()) -> AuthorityScope:
    return AuthorityScope(
        workspace_id=OpaqueId("workspace-a"),
        channel_id=OpaqueId("channel-a"),
        concept_id=OpaqueId("concept-a"),
        episode_id=OpaqueId("episode-a"),
        provider_id=OpaqueId("provider-a"),
        model_id=OpaqueId("model-a"),
        destination="destination-a",
        cost_minor_units=100,
        currency="USD",
        candidate_count=2,
        retry_index=0,
        input_artifacts=inputs,
        allowed_outputs=(OutputScope("outputs", ("media-output/1.0",)),),
    )


def _request(action_id: str = "run_external_generation", *, profiles=None, scope=None):
    plan = _plan(action_id)
    return build_action_authority_request(
        request_id="request-a",
        request_envelope_sha256="1" * 64,
        idempotency_key="idempotency-a",
        plan=plan,
        profiles=profiles or ProfileSelection(AssuranceProfile.PRODUCTION, AutonomyProfile.ASSISTED),
        scope=scope or _scope(inputs=(_ref("inputs/packet.json"),)),
    )


class FakeLedger:
    def __init__(self, source: AuthoritySource, principals: tuple[str, ...]) -> None:
        self.source = source
        self.principals = principals
        self.deny = False
        self.kill_switch_clear = True
        self.purpose_override: VerificationPurpose | None = None
        self.workspace_observation_override: str | None = None
        self.omit_signature_verification = False

    def _receipt(
        self,
        request,
        risk,
        *,
        purpose: VerificationPurpose,
        evaluated_at: datetime,
        decision_sha256=None,
        adapter_id=None,
        service_identity=None,
        grant_sha256=None,
        workspace_observation_sha256=None,
    ):
        provisional = AuthorityVerificationReceipt(
            artifact_version="authority-verification-receipt/1.0",
            receipt_id=OpaqueId("pending"),
            receipt_sha256=HashDigest("0" * 64),
            purpose=self.purpose_override or purpose,
            action_request_sha256=request.request_sha256,
            authority_decision_sha256=decision_sha256,
            gate_context_sha256=gate_context_sha256(request.gate_context),
            risk_assessment_sha256=risk,
            authority_source=self.source,
            ledger_state=LedgerRecordState.ACTIVE,
            ledger_head_sha256=HashDigest("2" * 64),
            ledger_entry=_ref("ledger/entry.json", "3", "authority-ledger-entry/1.0"),
            grant_sha256=grant_sha256,
            principal_ids=tuple(OpaqueId(item) for item in self.principals),
            signature_verification_refs=(
                ()
                if self.omit_signature_verification
                else (
                    _ref(
                        "ledger/signature.json",
                        "4",
                        "signature-verification/1.0",
                    ),
                )
            ),
            revocation_checked_at=evaluated_at.isoformat(),
            kill_switch_clear=self.kill_switch_clear,
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
            adapter_id=OpaqueId(adapter_id) if adapter_id else None,
            service_identity=OpaqueId(service_identity) if service_identity else None,
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
        self, request, risk, presented_grant, authority_references, *, current_context, evaluated_at
    ):
        if self.deny:
            return None
        return self._receipt(
            request,
            risk.assessment_sha256,
            purpose=VerificationPurpose.INITIAL_DECISION,
            evaluated_at=evaluated_at,
            grant_sha256=(presented_grant.authorization_sha256 if presented_grant else None),
        )

    def revalidate_and_reserve_current(
        self, decision, request, *, current_context, workspace_observation_sha256,
        adapter_id, service_identity, evaluated_at, purpose
    ):
        if self.deny:
            return None
        return self._receipt(
            request,
            decision.risk_assessment_sha256,
            purpose=purpose,
            evaluated_at=evaluated_at,
            decision_sha256=decision.decision_sha256,
            adapter_id=str(adapter_id),
            service_identity=str(service_identity),
            workspace_observation_sha256=(
                self.workspace_observation_override
                or workspace_observation_sha256
            ),
        )


def _standing_grant(request) -> UnverifiedStandingAuthorization:
    provisional = UnverifiedStandingAuthorization(
        artifact_version="standing-authorization/1.0",
        authorization_id=OpaqueId("pending"),
        authorization_sha256=HashDigest("0" * 64),
        gate_context_sha256=gate_context_sha256(request.gate_context),
        capability_ids=(request.capability_id,),
        workspace_ids=(request.scope.workspace_id,),
        channel_ids=(request.scope.channel_id,),
        concept_ids=(request.scope.concept_id,),
        episode_ids=(request.scope.episode_id,),
        provider_ids=(request.scope.provider_id,) if request.scope.provider_id else (),
        model_ids=(request.scope.model_id,) if request.scope.model_id else (),
        destinations=(request.scope.destination,) if request.scope.destination else (),
        max_cost_per_run_minor=request.scope.cost_minor_units,
        max_cost_per_day_minor=10_000,
        currency=request.scope.currency,
        max_candidates=request.scope.candidate_count,
        max_retries=request.scope.retry_index,
        allowed_risks=(request.action_risk,),
        minimum_assurance=request.profiles.assurance,
        maximum_autonomy=request.profiles.autonomy,
        exact_input_artifacts=request.scope.input_artifacts,
        allowed_outputs=request.scope.allowed_outputs,
        valid_from=(NOW - timedelta(minutes=1)).isoformat(),
        expires_at=(NOW + timedelta(minutes=5)).isoformat(),
        ledger_record=_ref(
            "ledger/grant.json", "1", "authority-ledger-entry/1.0"
        ),
        signature_verification_refs=(
            _ref(
                "ledger/grant-signature.json",
                "2",
                "signature-verification/1.0",
            ),
        ),
        authority_effect="none",
    )
    digest = standing_authorization_sha256(provisional)
    return replace(
        provisional,
        authorization_id=OpaqueId(f"standing-authorization-{str(digest)[:20]}"),
        authorization_sha256=digest,
    )


def test_missing_ledger_or_human_evidence_never_authorizes_r2() -> None:
    request = _request()
    risk, decision = evaluate_authority(
        request, target_policy_bundle(), ledger=None, evaluated_at=NOW
    )
    assert risk.effective_risk is ActionRisk.R2
    assert decision.status is AuthorityDecisionStatus.HUMAN_APPROVAL_REQUIRED
    assert decision.authority_effect == "none"
    approval = build_approval_request(request, risk, decision)
    assert isinstance(approval, ApprovalRequest)
    assert approval.creates_authority is False
    assert validate_artifact_mapping(approval_request_to_mapping(approval)).ok
    assert authority_artifact_from_bytes(authority_artifact_to_bytes(approval)) == approval


@pytest.mark.parametrize("assurance", list(AssuranceProfile))
@pytest.mark.parametrize("autonomy", list(AutonomyProfile))
def test_profile_or_mode_names_never_replace_missing_authority(
    assurance: AssuranceProfile,
    autonomy: AutonomyProfile,
) -> None:
    request = _request(profiles=ProfileSelection(assurance, autonomy))
    _, decision = evaluate_authority(
        request, target_policy_bundle(), ledger=None, evaluated_at=NOW
    )
    assert decision.status is not AuthorityDecisionStatus.AUTHORIZED


def test_current_one_human_ledger_authorizes_r2_but_dispatch_revalidates_fresh() -> None:
    request = _request()
    ledger = FakeLedger(AuthoritySource.ONE_SHOT_HUMAN, ("human-a",))
    risk, decision = evaluate_authority(
        request,
        target_policy_bundle(),
        ledger=ledger,
        authority_references=(_ref("ledger/human-a.json", "5", "human-approval/1.0"),),
        evaluated_at=NOW,
    )
    assert decision.status is AuthorityDecisionStatus.AUTHORIZED
    assert decision.source is AuthoritySource.ONE_SHOT_HUMAN
    assert decision.authority_effect == "execution_authority"
    assert validate_artifact_mapping(authority_decision_to_mapping(decision)).ok
    assert authority_artifact_from_bytes(authority_artifact_to_bytes(decision)) == decision

    verified = revalidate_authority_for_side_effect(
        decision,
        request,
        ledger=ledger,
        current_context=request.gate_context,
        workspace_observation_sha256="6" * 64,
        adapter_id="executor-a",
        service_identity="service-a",
        evaluated_at=NOW + timedelta(seconds=1),
        purpose=VerificationPurpose.DISPATCH,
    )
    assert verified.purpose is VerificationPurpose.DISPATCH
    assert validate_artifact_mapping(
        authority_verification_receipt_to_mapping(verified.receipt)
    ).ok

    ledger.deny = True
    with pytest.raises(AuthorityContractError, match="denied"):
        revalidate_authority_for_side_effect(
            decision,
            request,
            ledger=ledger,
            current_context=request.gate_context,
            workspace_observation_sha256="6" * 64,
            adapter_id="executor-a",
            service_identity="service-a",
            evaluated_at=NOW + timedelta(seconds=2),
            purpose=VerificationPurpose.DISPATCH,
        )


def test_dispatch_receipt_cannot_be_replayed_for_reconcile() -> None:
    request = _request()
    ledger = FakeLedger(AuthoritySource.ONE_SHOT_HUMAN, ("human-a",))
    _, decision = evaluate_authority(
        request, target_policy_bundle(), ledger=ledger,
        authority_references=(_ref("ledger/human-a.json", "5", "human-approval/1.0"),),
        evaluated_at=NOW,
    )
    ledger.purpose_override = VerificationPurpose.DISPATCH
    with pytest.raises(AuthorityContractError, match="another action"):
        revalidate_authority_for_side_effect(
            decision, request, ledger=ledger, current_context=request.gate_context,
            workspace_observation_sha256="6" * 64, adapter_id="executor-a",
            service_identity="service-a", evaluated_at=NOW + timedelta(seconds=1),
            purpose=VerificationPurpose.RECONCILE,
        )


def test_each_material_context_change_invalidates_decision_before_ledger_call() -> None:
    request = _request()
    ledger = FakeLedger(AuthoritySource.ONE_SHOT_HUMAN, ("human-a",))
    _, decision = evaluate_authority(
        request, target_policy_bundle(), ledger=ledger,
        authority_references=(_ref("ledger/human-a.json", "5", "human-approval/1.0"),),
        evaluated_at=NOW,
    )
    for field in (
        "workflow_definition_sha256", "policy_bundle_sha256", "rules_bundle_sha256",
        "effective_config_sha256", "current_manifest_sha256", "evidence_graph_sha256",
        "executable_plan_sha256",
    ):
        changed = replace(request.gate_context, **{field: HashDigest("9" * 64)})
        with pytest.raises(AuthorityContractError, match="context"):
            revalidate_authority_for_side_effect(
                decision, request, ledger=ledger, current_context=changed,
                workspace_observation_sha256="6" * 64, adapter_id="executor-a",
                service_identity="service-a", evaluated_at=NOW + timedelta(seconds=1),
                purpose=VerificationPurpose.DISPATCH,
            )


def test_scope_rejects_case_alias_and_conflicting_identity() -> None:
    scope = _scope(
        inputs=(
            _ref("Artifacts/Input.json", "a"),
            _ref("artifacts/input.json", "b"),
        )
    )
    with pytest.raises(AuthorityContractError, match="collide"):
        _request(scope=scope)


@pytest.mark.parametrize(
    "path",
    ("CON", "a/CONOUT$", "a/file.json:stream", "a/trailing. "),
)
def test_scope_rejects_cross_platform_unsafe_paths(path: str) -> None:
    with pytest.raises(AuthorityContractError, match="path"):
        _request(scope=_scope(inputs=(_ref(path),)))

    with pytest.raises(AuthorityContractError, match="path"):
        _request(
            scope=replace(
                _scope(),
                allowed_outputs=(OutputScope(path, ("media-output/1.0",)),),
            )
        )


def test_output_scope_rejects_ancestor_overlap() -> None:
    with pytest.raises(AuthorityContractError, match="output"):
        _request(
            scope=replace(
                _scope(),
                allowed_outputs=(
                    OutputScope("outputs", ("media-output/1.0",)),
                    OutputScope("outputs/sub", ("media-output/1.0",)),
                ),
            )
        )


def test_human_required_r0_action_uses_requirement_not_risk_for_source() -> None:
    request = _request("approve_generation")
    risk, missing = evaluate_authority(
        request,
        target_policy_bundle(),
        ledger=None,
        evaluated_at=NOW,
    )
    assert risk.effective_risk is ActionRisk.R0
    assert request.authority_requirement is AuthorityRequirement.HUMAN_OR_CAMPAIGN
    assert missing.status is AuthorityDecisionStatus.HUMAN_APPROVAL_REQUIRED

    ledger = FakeLedger(AuthoritySource.ONE_SHOT_HUMAN, ("human-a",))
    _, granted = evaluate_authority(
        request,
        target_policy_bundle(),
        ledger=ledger,
        authority_references=(
            _ref("ledger/human-a.json", "5", "human-approval/1.0"),
        ),
        evaluated_at=NOW,
    )
    assert granted.status is AuthorityDecisionStatus.AUTHORIZED
    assert granted.source is AuthoritySource.ONE_SHOT_HUMAN


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    (
        ("workspace_id", OpaqueId("workspace-b"), "authority.scope.workspace"),
        ("channel_id", OpaqueId("channel-b"), "authority.scope.channel"),
        ("concept_id", OpaqueId("concept-b"), "authority.scope.concept"),
        ("episode_id", OpaqueId("episode-b"), "authority.scope.episode"),
        ("provider_id", OpaqueId("provider-b"), "authority.scope.provider"),
        ("model_id", OpaqueId("model-b"), "authority.scope.model"),
        ("destination", "destination-b", "authority.scope.destination"),
        ("cost_minor_units", 101, "authority.scope.cost_run"),
        ("currency", "EUR", "authority.scope.cost_run"),
        ("candidate_count", 3, "authority.scope.candidates"),
        ("retry_index", 1, "authority.scope.retries"),
        (
            "input_artifacts",
            (_ref("inputs/other.json"),),
            "authority.scope.inputs",
        ),
        (
            "allowed_outputs",
            (OutputScope("other-outputs", ("media-output/1.0",)),),
            "authority.scope.outputs",
        ),
    ),
)
def test_standing_grant_scope_mutations_fail_closed(
    field: str,
    value: object,
    reason: str,
) -> None:
    base = _request()
    grant = _standing_grant(base)
    ledger = FakeLedger(AuthoritySource.STANDING_GRANT, ())
    _, exact = evaluate_authority(
        base,
        target_policy_bundle(),
        ledger=ledger,
        presented_grant=grant,
        evaluated_at=NOW,
    )
    assert exact.status is AuthorityDecisionStatus.AUTHORIZED

    changed = _request(scope=replace(base.scope, **{field: value}))
    _, denied = evaluate_authority(
        changed,
        target_policy_bundle(),
        ledger=ledger,
        presented_grant=grant,
        evaluated_at=NOW,
    )
    assert denied.status is AuthorityDecisionStatus.DENIED
    assert reason in denied.reason_codes


def test_signature_and_workspace_receipt_rebinding_fail_closed() -> None:
    request = _request()
    ledger = FakeLedger(AuthoritySource.ONE_SHOT_HUMAN, ("human-a",))
    ledger.omit_signature_verification = True
    with pytest.raises(AuthorityContractError, match="signature"):
        evaluate_authority(
            request,
            target_policy_bundle(),
            ledger=ledger,
            authority_references=(
                _ref("ledger/human-a.json", "5", "human-approval/1.0"),
            ),
            evaluated_at=NOW,
        )

    ledger.omit_signature_verification = False
    _, decision = evaluate_authority(
        request,
        target_policy_bundle(),
        ledger=ledger,
        authority_references=(
            _ref("ledger/human-a.json", "5", "human-approval/1.0"),
        ),
        evaluated_at=NOW,
    )
    ledger.workspace_observation_override = "9" * 64
    with pytest.raises(AuthorityContractError, match="another action"):
        revalidate_authority_for_side_effect(
            decision,
            request,
            ledger=ledger,
            current_context=request.gate_context,
            workspace_observation_sha256="6" * 64,
            adapter_id="executor-a",
            service_identity="service-a",
            evaluated_at=NOW + timedelta(seconds=1),
            purpose=VerificationPurpose.DISPATCH,
        )


def test_w02_bridge_is_fixed_r4_and_needs_dual_human_ledger() -> None:
    policy = target_policy_bundle()
    context = GateContext(
        workflow_definition_sha256=default_workflow_definition().definition_sha256,
        policy_bundle_sha256=policy.bundle_sha256,
        rules_bundle_sha256=HashDigest("1" * 64),
        effective_config_sha256=HashDigest("2" * 64),
        current_manifest_sha256=HashDigest("3" * 64),
        evidence_graph_sha256=HashDigest("4" * 64),
        executable_plan_sha256=HashDigest("5" * 64),
    )
    request = build_bound_action_authority_request(
        request_id="mutation-request",
        request_envelope_sha256="6" * 64,
        idempotency_key="mutation-key",
        action_id="managed_mutation",
        capability_id="managed_mutation",
        executable_plan_sha256="5" * 64,
        action_risk=ActionRisk.R4,
        authority_requirement=AuthorityRequirement.TWO_INDEPENDENT_HUMANS,
        side_effect=True,
        gate_context=context,
        profiles=ProfileSelection(AssuranceProfile.HIGH_ASSURANCE, AutonomyProfile.ASSISTED),
        scope=_scope(),
    )
    risk, missing = evaluate_authority(request, policy, ledger=None, evaluated_at=NOW)
    assert risk.effective_risk is ActionRisk.R4
    assert missing.status is AuthorityDecisionStatus.HUMAN_APPROVAL_REQUIRED
    assert build_approval_request(request, risk, missing).required_independent_humans == 2

    ledger = FakeLedger(AuthoritySource.DUAL_HUMAN, ("human-a", "human-b"))
    _, granted = evaluate_authority(
        request, policy, ledger=ledger,
        authority_references=(
            _ref("ledger/human-a.json", "7", "human-approval/1.0"),
            _ref("ledger/human-b.json", "8", "human-approval/1.0"),
        ),
        evaluated_at=NOW,
    )
    assert granted.status is AuthorityDecisionStatus.AUTHORIZED
    assert granted.source is AuthoritySource.DUAL_HUMAN


def test_r4_standing_authorization_is_rejected_before_ledger() -> None:
    provisional = UnverifiedStandingAuthorization(
        artifact_version="standing-authorization/1.0",
        authorization_id=OpaqueId("pending"),
        authorization_sha256=HashDigest("0" * 64),
        gate_context_sha256=HashDigest("1" * 64),
        capability_ids=(OpaqueId("managed_mutation"),),
        workspace_ids=(OpaqueId("workspace-a"),),
        channel_ids=(OpaqueId("channel-a"),),
        concept_ids=(OpaqueId("concept-a"),),
        episode_ids=(OpaqueId("episode-a"),),
        provider_ids=(), model_ids=(), destinations=(),
        max_cost_per_run_minor=0, max_cost_per_day_minor=0, currency="USD",
        max_candidates=0, max_retries=0,
        allowed_risks=(ActionRisk.R4,),
        minimum_assurance=AssuranceProfile.PRODUCTION,
        maximum_autonomy=AutonomyProfile.ASSISTED,
        exact_input_artifacts=(),
        allowed_outputs=(),
        valid_from=NOW.isoformat(), expires_at=(NOW + timedelta(minutes=5)).isoformat(),
        ledger_record=_ref("ledger/grant.json", "1", "authority-ledger-entry/1.0"),
        signature_verification_refs=(_ref("ledger/signature.json", "2", "signature-verification/1.0"),),
        authority_effect="none",
    )
    digest = standing_authorization_sha256(provisional)
    grant = replace(
        provisional,
        authorization_id=OpaqueId(f"standing-authorization-{str(digest)[:20]}"),
        authorization_sha256=digest,
    )
    with pytest.raises(AuthorityContractError, match="R4"):
        validate_standing_authorization(grant)


def test_legacy_generation_readiness_cannot_self_assert_authority() -> None:
    readiness = GenerationReadinessPlan(
        ready=True,
        packet=_ref("artifacts/packet.json", "1", "generation-packet/2.0"),
        packet_content_sha256="2" * 64,
        feasibility_review=_ref("artifacts/feasibility.json", "3", "generation-feasibility-review/2.0"),
        approval_evidence=_ref("artifacts/approval.json", "4", "packet-approval/2.0"),
        blockers=(),
        gate_context_sha256="5" * 64,
        authorization_ready=True,
        valid_from=NOW.isoformat(),
        valid_until=(NOW + timedelta(minutes=5)).isoformat(),
        workspace_revision_id="revision-a",
        workspace_id="workspace-a",
        workspace_revision_sha256="6" * 64,
        workspace_observation_sha256="7" * 64,
    )
    assert readiness.ready is True
    assert readiness.authorization_ready is False
    assert readiness.authority_effect == "none"
    assert readiness.requires_authority_decision is True
