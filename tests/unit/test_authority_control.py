"""W04 current-ledger authority and pre-side-effect receipt boundaries."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

import pytest
import video_factory.authority.evaluator as authority_evaluator

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
    HardEscalationFact,
    HardEscalationState,
    LedgerRecordState,
    OutputScope,
    PrincipalSignatureVerification,
    ProfileSelection,
    UnverifiedStandingAuthorization,
    VerificationPurpose,
    action_authority_request_to_mapping,
    approval_request_to_mapping,
    authority_artifact_from_bytes,
    authority_artifact_to_bytes,
    authority_decision_to_mapping,
    authority_verification_receipt_sha256,
    authority_verification_receipt_to_mapping,
    build_action_authority_request,
    build_approval_request,
    build_bound_action_authority_request,
    classify_action_risk,
    evaluate_authority,
    revalidate_authority_for_side_effect,
    standing_authorization_to_mapping,
    standing_authorization_sha256,
    target_policy_bundle,
    validate_action_authority_request,
    validate_approval_request,
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
    WorkflowContractError,
    build_executable_production_plan,
    build_gate_result,
    default_workflow_definition,
    evaluate_workflow,
    gate_consumed_context_sha256,
)


NOW = datetime(2026, 8, 8, 0, 0, tzinfo=timezone.utc)


def _ref(path: str, digest: str = "a", version: str = "brief/1.0") -> ArtifactReference:
    return ArtifactReference(
        path=RelativeArtifactPath(path),
        sha256=HashDigest(digest * 64),
        artifact_version=ArtifactVersion(version),
    )


def _hard_escalation_facts(
    overrides: dict[str, HardEscalationState] | None = None,
) -> tuple[HardEscalationFact, ...]:
    states = overrides or {}
    return tuple(
        HardEscalationFact(
            trigger=trigger,
            state=states.get(trigger, HardEscalationState.CLEAR),
            evidence_refs=(
                _ref(
                    f"authority/facts/{index:02d}-{trigger}.json",
                    format(index, "x"),
                    "policy-fact-observation/1.0",
                ),
            ),
        )
        for index, trigger in enumerate(
            target_policy_bundle().hard_escalation_triggers, start=1
        )
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
    context = MaterialContextSeed(
        workflow_definition_sha256=definition.definition_sha256,
        policy_bundle_sha256=policy.bundle_sha256,
        rules_bundle_sha256=HashDigest("c" * 64),
        effective_config_sha256=HashDigest("d" * 64),
        current_manifest_sha256=HashDigest("e" * 64),
        evidence_graph_sha256=HashDigest("f" * 64),
    )
    gates = {str(item.gate_id): item for item in definition.gates}
    results = []
    for claim in definition.claims:
        target = claim.claim_id == action.satisfies_claim_id
        if str(claim.claim_id) in earlier_claims:
            results.append(build_gate_result(
                str(claim.gate_id), GateStatus.PASS,
                consumed_context_sha256=str(gate_consumed_context_sha256(gates[str(claim.gate_id)], context)),
                evidence_sha256s=("b" * 64,),
            ))
        elif target:
            reason = action.trigger_reason_codes[0] if action.trigger_reason_codes else f"workflow.{claim.claim_id}.blocked"
            results.append(
                build_gate_result(
                    str(claim.gate_id), GateStatus.BLOCKED,
                    consumed_context_sha256=str(gate_consumed_context_sha256(gates[str(claim.gate_id)], context)),
                    reason_codes=(reason,), messages=("blocked",),
                )
            )
        else:
            results.append(
                build_gate_result(
                    str(claim.gate_id), GateStatus.UNKNOWN,
                    consumed_context_sha256=str(gate_consumed_context_sha256(gates[str(claim.gate_id)], context)),
                    reason_codes=("workflow.gate.missing",), messages=("missing",),
                )
            )
    evaluation = evaluate_workflow(definition, results, context)
    assert str(evaluation.recommended_action_id) == action_id
    return (
        build_executable_production_plan(definition, evaluation, action_id),
        evaluation,
    )


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


def _request(
    action_id: str = "run_external_generation",
    *,
    profiles=None,
    scope=None,
    requester_principal_id: str = "requester-a",
    hard_escalation_facts=None,
):
    plan, evaluation = _plan(action_id)
    return build_action_authority_request(
        request_id="request-a",
        request_envelope_sha256="1" * 64,
        idempotency_key="idempotency-a",
        requester_principal_id=requester_principal_id,
        plan=plan,
        workflow_evaluation=evaluation,
        profiles=profiles or ProfileSelection(AssuranceProfile.PRODUCTION, AutonomyProfile.ASSISTED),
        scope=scope or _scope(inputs=(_ref("inputs/packet.json"),)),
        hard_escalation_facts=(
            hard_escalation_facts
            if hard_escalation_facts is not None
            else _hard_escalation_facts()
        ),
    )


class FakeLedger:
    def __init__(self, source: AuthoritySource, principals: tuple[str, ...]) -> None:
        self.source = source
        self.principals = principals
        self.deny = False
        self.ledger_state = LedgerRecordState.ACTIVE
        self.kill_switch_clear = True
        self.purpose_override: VerificationPurpose | None = None
        self.workspace_observation_override: str | None = None
        self.omit_signature_verification = False
        self.shared_human_signature = False
        self.shared_human_signature_bytes = False
        self.requester_principal_override: str | None = None
        self.requester_authentication_digest = "9"
        self.risk_assessment_override: str | None = None
        self.workflow_evaluation_override: str | None = None
        self.workflow_verification_digest = "a"
        self.omit_workflow_verification = False
        self.trusted_workflow_evaluation_sha256: str | None = None
        self.remaining_daily_budget_minor: int | None = None
        self.verify_current_calls = 0
        self.revalidate_current_calls = 0
        self.validity_seconds = 300
        self.ledger_head_sha256 = "2" * 64
        self.ledger_entry_version = "authority-ledger-entry/1.0"
        self.requester_authentication_version = (
            "principal-authentication/1.0"
        )
        self.signature_verification_version = "signature-verification/1.0"

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
            risk_assessment_sha256=HashDigest(
                self.risk_assessment_override or str(risk)
            ),
            workflow_evaluation_sha256=(
                HashDigest(
                    self.workflow_evaluation_override
                    or str(request.workflow_evaluation.evaluation_sha256)
                )
                if request.workflow_evaluation is not None
                else None
            ),
            workflow_evaluation_verification_ref=(
                None
                if request.workflow_evaluation is None
                or self.omit_workflow_verification
                else _ref(
                    "ledger/workflow-evaluation-verification.json",
                    self.workflow_verification_digest,
                    "workflow-evaluation-verification/1.0",
                )
            ),
            authority_source=self.source,
            ledger_state=self.ledger_state,
            ledger_head_sha256=HashDigest(self.ledger_head_sha256),
            ledger_entry=_ref(
                "ledger/entry.json",
                "3",
                self.ledger_entry_version,
            ),
            requester_principal_id=OpaqueId(
                self.requester_principal_override
                or str(request.requester_principal_id)
            ),
            requester_authentication_ref=_ref(
                "ledger/requester-authentication.json",
                self.requester_authentication_digest,
                self.requester_authentication_version,
            ),
            grant_sha256=grant_sha256,
            principal_verifications=(
                ()
                if self.omit_signature_verification
                else tuple(
                    PrincipalSignatureVerification(
                        OpaqueId(principal),
                        _ref(
                            (
                                "ledger/signature-shared.json"
                                if self.shared_human_signature
                                else f"ledger/signature-{index}.json"
                            ),
                            (
                                "4"
                                if self.shared_human_signature
                                or self.shared_human_signature_bytes
                                else str(index + 4)
                            ),
                            self.signature_verification_version,
                        ),
                    )
                    for index, principal in enumerate(self.principals)
                )
            ),
            signature_verification_refs=(
                (
                    _ref(
                        "ledger/grant-signature.json",
                        "7",
                        self.signature_verification_version,
                    ),
                )
                if self.source in {
                    AuthoritySource.STANDING_GRANT,
                    AuthoritySource.RELEASE_CAMPAIGN,
                }
                and not self.omit_signature_verification
                else ()
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
            valid_until=(
                evaluated_at + timedelta(seconds=self.validity_seconds)
            ).isoformat(),
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
        self.verify_current_calls += 1
        if self.deny or (
            self.trusted_workflow_evaluation_sha256 is not None
            and (
                request.workflow_evaluation is None
                or str(request.workflow_evaluation.evaluation_sha256)
                != self.trusted_workflow_evaluation_sha256
            )
        ):
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
        self.revalidate_current_calls += 1
        if self.deny or (
            self.remaining_daily_budget_minor is not None
            and request.scope.cost_minor_units
            > self.remaining_daily_budget_minor
        ):
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


def _rehash_standing_grant(
    grant: UnverifiedStandingAuthorization,
    **changes: object,
) -> UnverifiedStandingAuthorization:
    provisional = replace(
        grant,
        authorization_id=OpaqueId("pending"),
        authorization_sha256=HashDigest("0" * 64),
        **changes,
    )
    digest = standing_authorization_sha256(provisional)
    return replace(
        provisional,
        authorization_id=OpaqueId(
            f"standing-authorization-{str(digest)[:20]}"
        ),
        authorization_sha256=digest,
    )


def _r4_request():
    policy = target_policy_bundle()
    context = GateContext(
        workflow_definition_sha256=(
            default_workflow_definition().definition_sha256
        ),
        policy_bundle_sha256=policy.bundle_sha256,
        rules_bundle_sha256=HashDigest("1" * 64),
        effective_config_sha256=HashDigest("2" * 64),
        current_manifest_sha256=HashDigest("3" * 64),
        evidence_graph_sha256=HashDigest("4" * 64),
        executable_plan_sha256=HashDigest("5" * 64),
    )
    return build_bound_action_authority_request(
        request_id="matrix-mutation-request",
        request_envelope_sha256="6" * 64,
        idempotency_key="matrix-mutation-key",
        requester_principal_id="mutation-requester",
        action_id="managed_mutation",
        capability_id="managed_mutation",
        executable_plan_sha256="5" * 64,
        action_risk=ActionRisk.R4,
        authority_requirement=AuthorityRequirement.TWO_INDEPENDENT_HUMANS,
        side_effect=True,
        gate_context=context,
        profiles=ProfileSelection(
            AssuranceProfile.HIGH_ASSURANCE,
            AutonomyProfile.ASSISTED,
        ),
        scope=_scope(),
        hard_escalation_facts=_hard_escalation_facts(),
    )


def _authorize_r2():
    request = _request()
    ledger = FakeLedger(AuthoritySource.ONE_SHOT_HUMAN, ("human-a",))
    risk, decision = evaluate_authority(
        request,
        target_policy_bundle(),
        ledger=ledger,
        authority_references=(
            _ref("ledger/human-a.json", "5", "human-approval/1.0"),
        ),
        evaluated_at=NOW,
    )
    assert decision.status is AuthorityDecisionStatus.AUTHORIZED
    return request, ledger, decision


def _authorize_standing():
    request = _request()
    grant = _standing_grant(request)
    ledger = FakeLedger(AuthoritySource.STANDING_GRANT, ())
    risk, decision = evaluate_authority(
        request,
        target_policy_bundle(),
        ledger=ledger,
        presented_grant=grant,
        evaluated_at=NOW,
    )
    assert decision.status is AuthorityDecisionStatus.AUTHORIZED
    return request, grant, ledger, decision


def _revalidate_r2(request, ledger, decision) -> None:
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
    assert verified.request_sha256 == request.request_sha256


def _assert_authority_error(expected_reason: str, operation) -> None:
    with pytest.raises(AuthorityContractError) as caught:
        operation()
    assert caught.value.reason_code == expected_reason


def _assert_matrix_positive(policy_path: str) -> None:
    if policy_path == "ai_human_approval_creation_forbidden":
        request = _request()
        risk, decision = evaluate_authority(
            request,
            target_policy_bundle(),
            ledger=None,
            evaluated_at=NOW,
        )
        approval = build_approval_request(request, risk, decision)
        assert validate_approval_request(approval).creates_authority is False
        assert approval.authority_effect == "none"
        return
    if policy_path.startswith("scope.") and policy_path != "scope.cost_per_day":
        _authorize_standing()
        return
    if policy_path in {
        "executor_revalidation_required",
        "material_change_invalidates_authority",
        "scope.cost_per_day",
        "revocation",
        "kill_switch",
    }:
        request, ledger, decision = _authorize_r2()
        _revalidate_r2(request, ledger, decision)
        return
    if policy_path == "validity":
        _authorize_standing()
        return
    if policy_path.startswith("R4."):
        request = _r4_request()
        ledger = FakeLedger(
            AuthoritySource.DUAL_HUMAN,
            ("human-a", "human-b"),
        )
        _, decision = evaluate_authority(
            request,
            target_policy_bundle(),
            ledger=ledger,
            authority_references=(
                _ref("ledger/human-a.json", "7", "human-approval/1.0"),
                _ref("ledger/human-b.json", "8", "human-approval/1.0"),
            ),
            evaluated_at=NOW,
        )
        assert decision.status is AuthorityDecisionStatus.AUTHORIZED
        if policy_path == "R4.short_expiry":
            _revalidate_r2(request, ledger, decision)
            assert ledger.verify_current_calls == 1
            assert ledger.revalidate_current_calls == 1
        return
    _authorize_r2()


def _assert_standing_scope_denied(
    grant: UnverifiedStandingAuthorization,
    request,
    expected_reason: str,
) -> None:
    _, denied = evaluate_authority(
        request,
        target_policy_bundle(),
        ledger=FakeLedger(AuthoritySource.STANDING_GRANT, ()),
        presented_grant=grant,
        evaluated_at=NOW,
    )
    assert denied.status is AuthorityDecisionStatus.DENIED
    assert expected_reason in denied.reason_codes


def _assert_matrix_negative(policy_path: str, expected_reason: str) -> None:
    if policy_path.startswith("hard_escalation_triggers."):
        trigger = policy_path.split(".", 1)[1]
        request = _request(
            hard_escalation_facts=_hard_escalation_facts(
                {trigger: HardEscalationState.TRIGGERED}
            )
        )
        risk, decision = evaluate_authority(
            request,
            target_policy_bundle(),
            ledger=None,
            evaluated_at=NOW,
        )
        approval = build_approval_request(request, risk, decision)
        assert risk.supported is True
        assert risk.effective_risk is ActionRisk.R4
        assert decision.status is AuthorityDecisionStatus.HUMAN_APPROVAL_REQUIRED
        assert decision.required_authority is AuthorityRequirement.TWO_INDEPENDENT_HUMANS
        assert expected_reason in decision.reason_codes
        assert approval.required_independent_humans == 2
        return
    if policy_path == "default_decision":
        request = _request()
        risk, decision = evaluate_authority(
            request,
            target_policy_bundle(),
            ledger=None,
            evaluated_at=NOW,
        )
        approval = build_approval_request(request, risk, decision)
        assert decision.status is AuthorityDecisionStatus.HUMAN_APPROVAL_REQUIRED
        assert expected_reason in decision.reason_codes
        assert approval.safe_default is AuthorityDecisionStatus.DENIED
        return
    if policy_path == "unknown_state_behavior":
        trigger = target_policy_bundle().hard_escalation_triggers[0]
        request = _request(
            hard_escalation_facts=_hard_escalation_facts(
                {trigger: HardEscalationState.UNKNOWN}
            )
        )
        risk, decision = evaluate_authority(
            request,
            target_policy_bundle(),
            ledger=None,
            evaluated_at=NOW,
        )
        assert risk.supported is False
        assert decision.status is AuthorityDecisionStatus.DENIED
        assert expected_reason in decision.reason_codes
        return
    if policy_path == "ai_human_approval_creation_forbidden":
        request = _request()
        risk, decision = evaluate_authority(
            request,
            target_policy_bundle(),
            ledger=None,
            evaluated_at=NOW,
        )
        approval = build_approval_request(request, risk, decision)
        _assert_authority_error(
            expected_reason,
            lambda: validate_approval_request(
                replace(approval, creates_authority=True)
            ),
        )
        return
    if policy_path == "self_approval_forbidden":
        request = _request(requester_principal_id="human-a")
        _, decision = evaluate_authority(
            request,
            target_policy_bundle(),
            ledger=FakeLedger(
                AuthoritySource.ONE_SHOT_HUMAN,
                ("human-a",),
            ),
            authority_references=(
                _ref("ledger/human-a.json", "5", "human-approval/1.0"),
            ),
            evaluated_at=NOW,
        )
        assert decision.status is AuthorityDecisionStatus.DENIED
        assert expected_reason in decision.reason_codes
        return
    if policy_path == "release_campaign.activation":
        request = _request()
        _, decision = evaluate_authority(
            request,
            target_policy_bundle(),
            ledger=FakeLedger(AuthoritySource.RELEASE_CAMPAIGN, ()),
            authority_references=(
                _ref("ledger/campaign.json", "5", "release-campaign/1.0"),
            ),
            evaluated_at=NOW,
        )
        assert decision.status is AuthorityDecisionStatus.DENIED
        assert expected_reason in decision.reason_codes
        return
    if policy_path in {
        "executor_revalidation_required",
        "material_change_invalidates_authority",
        "scope.cost_per_day",
        "revocation",
        "kill_switch",
    }:
        request, ledger, decision = _authorize_r2()
        current_context = request.gate_context
        if policy_path == "executor_revalidation_required":
            ledger = None
        elif policy_path == "material_change_invalidates_authority":
            current_context = replace(
                current_context,
                effective_config_sha256=HashDigest("9" * 64),
            )
        elif policy_path == "scope.cost_per_day":
            ledger.remaining_daily_budget_minor = (
                request.scope.cost_minor_units - 1
            )
        elif policy_path == "revocation":
            ledger.ledger_state = LedgerRecordState.REVOKED
        else:
            ledger.kill_switch_clear = False
        _assert_authority_error(
            expected_reason,
            lambda: revalidate_authority_for_side_effect(
                decision,
                request,
                ledger=ledger,
                current_context=current_context,
                workspace_observation_sha256="6" * 64,
                adapter_id="executor-a",
                service_identity="service-a",
                evaluated_at=NOW + timedelta(seconds=1),
                purpose=VerificationPurpose.DISPATCH,
            ),
        )
        return
    if policy_path.startswith("scope."):
        base = _request()
        grant = _standing_grant(base)
        if policy_path == "scope.capability":
            changed = base
            grant = _rehash_standing_grant(
                grant,
                capability_ids=(OpaqueId("other-capability"),),
            )
            reasons = ("authority.scope.capability",)
        elif policy_path == "scope.channel_concept_episode":
            reasons = (
                "authority.scope.channel",
                "authority.scope.concept",
                "authority.scope.episode",
            )
            for field, value, reason in (
                ("channel_id", OpaqueId("channel-b"), reasons[0]),
                ("concept_id", OpaqueId("concept-b"), reasons[1]),
                ("episode_id", OpaqueId("episode-b"), reasons[2]),
            ):
                changed = _request(
                    scope=replace(base.scope, **{field: value})
                )
                _assert_standing_scope_denied(grant, changed, reason)
            assert expected_reason == reasons[0]
            return
        elif policy_path == "scope.provider_model":
            reasons = ("authority.scope.provider", "authority.scope.model")
            for field, value, reason in (
                ("provider_id", OpaqueId("provider-b"), reasons[0]),
                ("model_id", OpaqueId("model-b"), reasons[1]),
            ):
                changed = _request(
                    scope=replace(base.scope, **{field: value})
                )
                _assert_standing_scope_denied(grant, changed, reason)
            assert expected_reason == reasons[0]
            return
        elif policy_path == "scope.destination":
            changed = _request(
                scope=replace(base.scope, destination="destination-b")
            )
            reasons = ("authority.scope.destination",)
        elif policy_path == "scope.cost_per_run":
            reasons = ("authority.scope.cost_run",)
            for field, value in (
                ("cost_minor_units", 101),
                ("currency", "EUR"),
            ):
                changed = _request(
                    scope=replace(base.scope, **{field: value})
                )
                _assert_standing_scope_denied(grant, changed, reasons[0])
            assert expected_reason == reasons[0]
            return
        elif policy_path == "scope.candidates":
            changed = _request(
                scope=replace(base.scope, candidate_count=3)
            )
            reasons = ("authority.scope.candidates",)
        elif policy_path == "scope.retries":
            changed = _request(scope=replace(base.scope, retry_index=1))
            reasons = ("authority.scope.retries",)
        else:
            raise AssertionError(f"unhandled policy row: {policy_path}")
        assert expected_reason == reasons[0]
        _assert_standing_scope_denied(grant, changed, expected_reason)
        return
    if policy_path == "validity":
        request = _request()
        grant = _rehash_standing_grant(
            _standing_grant(request),
            expires_at=NOW.isoformat(),
        )
        _, denied = evaluate_authority(
            request,
            target_policy_bundle(),
            ledger=FakeLedger(AuthoritySource.STANDING_GRANT, ()),
            presented_grant=grant,
            evaluated_at=NOW,
        )
        assert denied.status is AuthorityDecisionStatus.DENIED
        assert expected_reason in denied.reason_codes
        return
    if policy_path == "R4.independent_approvers":
        request = _r4_request()
        _, denied = evaluate_authority(
            request,
            target_policy_bundle(),
            ledger=FakeLedger(
                AuthoritySource.ONE_SHOT_HUMAN,
                ("human-a",),
            ),
            authority_references=(
                _ref("ledger/human-a.json", "7", "human-approval/1.0"),
            ),
            evaluated_at=NOW,
        )
        assert denied.status is AuthorityDecisionStatus.DENIED
        assert expected_reason in denied.reason_codes
        return
    if policy_path == "R4.standing_grant_forbidden":
        request = _r4_request()
        _assert_authority_error(
            expected_reason,
            lambda: validate_standing_authorization(
                _standing_grant(request)
            ),
        )
        return
    if policy_path == "R4.short_expiry":
        request = _r4_request()
        references = (
            _ref("ledger/human-a.json", "7", "human-approval/1.0"),
            _ref("ledger/human-b.json", "8", "human-approval/1.0"),
        )
        initial = FakeLedger(
            AuthoritySource.DUAL_HUMAN,
            ("human-a", "human-b"),
        )
        initial.validity_seconds = 301
        _assert_authority_error(
            expected_reason,
            lambda: evaluate_authority(
                request,
                target_policy_bundle(),
                ledger=initial,
                authority_references=references,
                evaluated_at=NOW,
            ),
        )
        ledger = FakeLedger(
            AuthoritySource.DUAL_HUMAN,
            ("human-a", "human-b"),
        )
        _, decision = evaluate_authority(
            request,
            target_policy_bundle(),
            ledger=ledger,
            authority_references=references,
            evaluated_at=NOW,
        )
        ledger.validity_seconds = 301
        _assert_authority_error(
            expected_reason,
            lambda: revalidate_authority_for_side_effect(
                decision,
                request,
                ledger=ledger,
                current_context=request.gate_context,
                workspace_observation_sha256="6" * 64,
                adapter_id="executor-a",
                service_identity="service-a",
                evaluated_at=NOW + timedelta(seconds=1),
                purpose=VerificationPurpose.MUTATION,
            ),
        )
        assert initial.verify_current_calls == 1
        assert ledger.verify_current_calls == 1
        assert ledger.revalidate_current_calls == 1
        return
    raise AssertionError(f"unhandled policy row: {policy_path}")


def test_policy_bundle_has_complete_exact_matrix() -> None:
    policy = target_policy_bundle()
    expected_paths = {
        "default_decision",
        "unknown_state_behavior",
        "ai_human_approval_creation_forbidden",
        "self_approval_forbidden",
        "release_campaign.activation",
        "executor_revalidation_required",
        "material_change_invalidates_authority",
        "scope.capability",
        "scope.channel_concept_episode",
        "scope.provider_model",
        "scope.destination",
        "scope.cost_per_run",
        "scope.cost_per_day",
        "scope.candidates",
        "scope.retries",
        "validity",
        "revocation",
        "kill_switch",
        "R4.independent_approvers",
        "R4.standing_grant_forbidden",
        "R4.short_expiry",
        *{
            f"hard_escalation_triggers.{trigger}"
            for trigger in policy.hard_escalation_triggers
        },
    }
    assert {item.policy_path for item in policy.enforcement_matrix} == expected_paths
    assert all(
        item.positive_test_id
        == f"test_policy_enforcement_matrix_positive[{item.policy_path}]"
        and item.negative_test_id
        == f"test_policy_enforcement_matrix_negative[{item.policy_path}]"
        and item.owner.split(".", 1)[0]
        in {"authority", "workflow", "mutation"}
        and item.enforcement_phase
        in {
            "initial",
            "serialization",
            "predispatch",
            "initial_and_predispatch",
        }
        for item in policy.enforcement_matrix
    )
    assert policy.self_approval_forbidden is True
    assert policy.release_campaign_enabled is False
    assert policy.maximum_r4_validity_seconds == 300
    by_path = {item.policy_path: item for item in policy.enforcement_matrix}
    assert by_path["material_change_invalidates_authority"].enforcement_phase == (
        "predispatch"
    )
    assert by_path["R4.short_expiry"].enforcement_phase == (
        "initial_and_predispatch"
    )
    assert all(
        by_path[f"hard_escalation_triggers.{trigger}"].owner
        == "authority.policy"
        and by_path[f"hard_escalation_triggers.{trigger}"].enforcement_phase
        == "initial"
        for trigger in policy.hard_escalation_triggers
    )
    root = Path(__file__).resolve().parents[2]
    assert str(policy.governance_policy_sha256) == sha256(
        (root / "docs/governance/authority-policy-v2.1.yaml").read_bytes()
    ).hexdigest()


@pytest.mark.parametrize(
    "row",
    target_policy_bundle().enforcement_matrix,
    ids=lambda row: row.policy_path,
)
def test_policy_enforcement_matrix_positive(row) -> None:
    assert row.positive_test_id == (
        f"test_policy_enforcement_matrix_positive[{row.policy_path}]"
    )
    _assert_matrix_positive(row.policy_path)


@pytest.mark.parametrize(
    "row",
    target_policy_bundle().enforcement_matrix,
    ids=lambda row: row.policy_path,
)
def test_policy_enforcement_matrix_negative(row) -> None:
    assert row.negative_test_id == (
        f"test_policy_enforcement_matrix_negative[{row.policy_path}]"
    )
    _assert_matrix_negative(row.policy_path, row.reason_code)


def test_requester_cannot_approve_own_action() -> None:
    request = _request(requester_principal_id="human-a")
    ledger = FakeLedger(AuthoritySource.ONE_SHOT_HUMAN, ("human-a",))
    risk, decision = evaluate_authority(
        request,
        target_policy_bundle(),
        ledger=ledger,
        authority_references=(
            _ref("ledger/human-a.json", "5", "human-approval/1.0"),
        ),
        evaluated_at=NOW,
    )
    assert decision.status is AuthorityDecisionStatus.DENIED
    assert decision.reason_codes == ("authority.self_approval_forbidden",)


def test_release_campaign_remains_fail_closed_until_w05_facts_exist() -> None:
    request = _request(
        "ready_for_human_publish",
        profiles=ProfileSelection(
            AssuranceProfile.DRAFT,
            AutonomyProfile.OBSERVE_ONLY,
        ),
    )
    _, decision = evaluate_authority(
        request,
        target_policy_bundle(),
        ledger=FakeLedger(AuthoritySource.RELEASE_CAMPAIGN, ()),
        authority_references=(
            _ref("ledger/campaign.json", "5", "release-campaign/1.0"),
        ),
        evaluated_at=NOW,
    )
    assert decision.status is AuthorityDecisionStatus.DENIED
    assert decision.reason_codes == ("authority.source.insufficient",)


def test_standing_allowlist_cannot_be_bypassed_with_absent_scope_value() -> None:
    exact = _request()
    with pytest.raises(AuthorityContractError, match="provider, model and destination"):
        _request(
            scope=replace(
                exact.scope,
                provider_id=None,
                model_id=None,
                destination=None,
            )
        )


@pytest.mark.parametrize(
    "state",
    (HardEscalationState.TRIGGERED, HardEscalationState.UNKNOWN),
)
@pytest.mark.parametrize(
    "trigger", target_policy_bundle().hard_escalation_triggers
)
def test_each_hard_escalation_fact_fails_closed(
    trigger: str,
    state: HardEscalationState,
) -> None:
    request = _request(
        hard_escalation_facts=_hard_escalation_facts({trigger: state})
    )
    risk, decision = evaluate_authority(
        request, target_policy_bundle(), ledger=None, evaluated_at=NOW
    )
    reason = f"authority.escalation.{trigger}.{state.value}"
    assert risk.effective_risk is ActionRisk.R4
    assert reason in risk.reason_codes
    assert reason in decision.reason_codes
    assert decision.authority_effect == "none"
    if state is HardEscalationState.TRIGGERED:
        assert risk.supported is True
        assert decision.status is AuthorityDecisionStatus.HUMAN_APPROVAL_REQUIRED
        assert decision.required_authority is AuthorityRequirement.TWO_INDEPENDENT_HUMANS
        assert build_approval_request(
            request, risk, decision
        ).required_independent_humans == 2
    else:
        assert risk.supported is False
        assert decision.status is AuthorityDecisionStatus.DENIED


def test_material_blueprint_change_escalates_routine_packet_work_to_r4() -> None:
    trigger = "material_blueprint_change_after_authorization"
    request = _request(
        "create_generation_packet",
        hard_escalation_facts=_hard_escalation_facts(
            {trigger: HardEscalationState.TRIGGERED}
        ),
    )
    risk, missing = evaluate_authority(
        request, target_policy_bundle(), ledger=None, evaluated_at=NOW
    )
    assert request.action_risk is ActionRisk.R1
    assert request.authority_requirement is AuthorityRequirement.POLICY
    assert risk.effective_risk is ActionRisk.R4
    assert risk.supported is True
    assert missing.status is AuthorityDecisionStatus.HUMAN_APPROVAL_REQUIRED
    assert missing.required_authority is AuthorityRequirement.TWO_INDEPENDENT_HUMANS
    assert build_approval_request(
        request, risk, missing
    ).required_independent_humans == 2

    ledger = FakeLedger(AuthoritySource.DUAL_HUMAN, ("human-a", "human-b"))
    _, granted = evaluate_authority(
        request,
        target_policy_bundle(),
        ledger=ledger,
        authority_references=(
            _ref("ledger/human-a.json", "7", "human-approval/1.0"),
            _ref("ledger/human-b.json", "8", "human-approval/1.0"),
        ),
        evaluated_at=NOW,
    )
    assert granted.status is AuthorityDecisionStatus.AUTHORIZED
    assert granted.required_authority is AuthorityRequirement.TWO_INDEPENDENT_HUMANS
    assert granted.source is AuthoritySource.DUAL_HUMAN


def test_material_escalation_remains_dual_human_at_predispatch() -> None:
    request = _request(
        "run_external_generation",
        hard_escalation_facts=_hard_escalation_facts(
            {
                "material_blueprint_change_after_authorization": (
                    HardEscalationState.TRIGGERED
                )
            }
        ),
    )
    ledger = FakeLedger(AuthoritySource.DUAL_HUMAN, ("human-a", "human-b"))
    risk, decision = evaluate_authority(
        request,
        target_policy_bundle(),
        ledger=ledger,
        authority_references=(
            _ref("ledger/human-a.json", "7", "human-approval/1.0"),
            _ref("ledger/human-b.json", "8", "human-approval/1.0"),
        ),
        evaluated_at=NOW,
    )
    assert risk.effective_risk is ActionRisk.R4
    assert decision.required_authority is AuthorityRequirement.TWO_INDEPENDENT_HUMANS
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
    assert verified.decision is decision
    assert verified.receipt.authority_source is AuthoritySource.DUAL_HUMAN


@pytest.mark.parametrize(
    "purpose",
    (
        VerificationPurpose.DISPATCH,
        VerificationPurpose.RECONCILE,
        VerificationPurpose.MUTATION,
    ),
)
def test_unsupported_risk_cannot_reach_predispatch_ledger(
    purpose: VerificationPurpose,
) -> None:
    trigger = "material_blueprint_change_after_authorization"
    request = _request(
        "run_external_generation",
        hard_escalation_facts=_hard_escalation_facts(
            {trigger: HardEscalationState.UNKNOWN}
        ),
    )
    policy = target_policy_bundle()
    risk = classify_action_risk(request, policy)
    assert risk.supported is False
    ledger = FakeLedger(AuthoritySource.DUAL_HUMAN, ("human-a", "human-b"))
    references = (
        _ref("ledger/human-a.json", "7", "human-approval/1.0"),
        _ref("ledger/human-b.json", "8", "human-approval/1.0"),
    )
    receipt = ledger.verify_current(
        request,
        risk,
        None,
        references,
        current_context=request.gate_context,
        evaluated_at=NOW,
    )
    assert receipt is not None
    # Simulate a structurally valid, self-rehashed caller document. A trusted
    # ledger must never see it once the current classifier says unsupported.
    decision = authority_evaluator._build_decision(
        request,
        risk,
        status=AuthorityDecisionStatus.AUTHORIZED,
        source=AuthoritySource.DUAL_HUMAN,
        reasons=("authority.test.self_rehashed",),
        evaluated_at=NOW,
        required_authority=AuthorityRequirement.TWO_INDEPENDENT_HUMANS,
        receipt=receipt,
    )
    with pytest.raises(AuthorityContractError) as rejected:
        revalidate_authority_for_side_effect(
            decision,
            request,
            ledger=ledger,
            current_context=request.gate_context,
            workspace_observation_sha256="6" * 64,
            adapter_id="executor-a",
            service_identity="service-a",
            evaluated_at=NOW + timedelta(seconds=1),
            purpose=purpose,
        )
    assert rejected.value.reason_code == "authority.predispatch.risk_unsupported"
    assert ledger.revalidate_current_calls == 0


def test_hard_escalation_fact_coverage_order_and_evidence_are_closed() -> None:
    facts = _hard_escalation_facts()
    with pytest.raises(AuthorityContractError, match="exact target policy order"):
        _request(hard_escalation_facts=tuple(reversed(facts)))
    with pytest.raises(AuthorityContractError, match="requires exact evidence"):
        _request(
            hard_escalation_facts=(
                replace(facts[0], evidence_refs=()),
                *facts[1:],
            )
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


def test_policy_workflow_authority_requires_trusted_evaluation_receipt() -> None:
    request = _request("create_storyboard")
    risk, missing = evaluate_authority(
        request,
        target_policy_bundle(),
        ledger=None,
        evaluated_at=NOW,
    )
    assert risk.effective_risk is ActionRisk.R1
    assert missing.status is AuthorityDecisionStatus.DENIED
    assert missing.authority_effect == "none"

    ledger = FakeLedger(AuthoritySource.POLICY, ())
    _, granted = evaluate_authority(
        request,
        target_policy_bundle(),
        ledger=ledger,
        evaluated_at=NOW,
    )
    assert granted.status is AuthorityDecisionStatus.AUTHORIZED
    assert granted.source is AuthoritySource.POLICY
    assert granted.verification_receipt_sha256 is not None
    assert granted.authority_basis_sha256 is not None


def test_incremental_workflow_authority_preserves_exact_predecessor_chain() -> None:
    definition = default_workflow_definition()
    _, baseline = _plan("run_external_generation")
    first_incremental = evaluate_workflow(
        definition,
        baseline.gate_results,
        baseline.material_context,
        previous=baseline,
    )
    incremental = evaluate_workflow(
        definition,
        first_incremental.gate_results,
        first_incremental.material_context,
        previous=first_incremental,
    )
    predecessors = (baseline, first_incremental)
    action_id = str(incremental.recommended_action_id)
    plan = build_executable_production_plan(
        definition,
        incremental,
        action_id,
        predecessors=predecessors,
    )
    request = build_action_authority_request(
        request_id="incremental-request",
        request_envelope_sha256="1" * 64,
        idempotency_key="incremental-idempotency",
        requester_principal_id="requester-a",
        plan=plan,
        workflow_evaluation=incremental,
        workflow_evaluation_predecessors=predecessors,
        profiles=ProfileSelection(
            AssuranceProfile.PRODUCTION,
            AutonomyProfile.ASSISTED,
        ),
        scope=_scope(inputs=(_ref("inputs/packet.json"),)),
        hard_escalation_facts=_hard_escalation_facts(),
    )
    assert action_authority_request_to_mapping(request)[
        "workflow_evaluation_predecessor_sha256s"
    ] == [
        str(baseline.evaluation_sha256),
        str(first_incremental.evaluation_sha256),
    ]

    ledger = FakeLedger(AuthoritySource.ONE_SHOT_HUMAN, ("human-a",))
    _, decision = evaluate_authority(
        request,
        target_policy_bundle(),
        ledger=ledger,
        authority_references=(
            _ref("ledger/human-a.json", "5", "human-approval/1.0"),
        ),
        evaluated_at=NOW,
    )
    assert decision.status is AuthorityDecisionStatus.AUTHORIZED
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

    without_predecessor = replace(
        request,
        workflow_evaluation_predecessors=(),
    )
    with pytest.raises(WorkflowContractError, match="exact predecessor"):
        validate_action_authority_request(without_predecessor)
    reordered = replace(
        request,
        workflow_evaluation_predecessors=tuple(reversed(predecessors)),
    )
    with pytest.raises(WorkflowContractError, match="oldest-to-newest"):
        validate_action_authority_request(reordered)


@pytest.mark.parametrize(
    ("attribute", "bad_value", "reason_code"),
    (
        (
            "ledger_entry_version",
            "brief/1.0",
            "authority.evidence.role",
        ),
        (
            "requester_authentication_version",
            "brief/1.0",
            "authority.evidence.role",
        ),
        (
            "signature_verification_version",
            "brief/1.0",
            "authority.evidence.role",
        ),
        (
            "ledger_head_sha256",
            "not-a-sha256",
            "authority.scope.digest",
        ),
    ),
)
def test_receipt_roles_and_ledger_head_fail_closed_initial_and_predispatch(
    attribute: str,
    bad_value: str,
    reason_code: str,
) -> None:
    request = _request()
    invalid_initial = FakeLedger(
        AuthoritySource.ONE_SHOT_HUMAN,
        ("human-a",),
    )
    setattr(invalid_initial, attribute, bad_value)
    with pytest.raises(AuthorityContractError) as initial:
        evaluate_authority(
            request,
            target_policy_bundle(),
            ledger=invalid_initial,
            authority_references=(
                _ref(
                    "ledger/human-a.json",
                    "5",
                    "human-approval/1.0",
                ),
            ),
            evaluated_at=NOW,
        )
    assert initial.value.reason_code == reason_code

    ledger = FakeLedger(AuthoritySource.ONE_SHOT_HUMAN, ("human-a",))
    risk, decision = evaluate_authority(
        request,
        target_policy_bundle(),
        ledger=ledger,
        authority_references=(
            _ref("ledger/human-a.json", "5", "human-approval/1.0"),
        ),
        evaluated_at=NOW,
    )
    receipt_document = authority_verification_receipt_to_mapping(
        ledger._receipt(
            request,
            risk.assessment_sha256,
            purpose=VerificationPurpose.INITIAL_DECISION,
            evaluated_at=NOW,
        )
    )
    if attribute == "ledger_entry_version":
        receipt_document["ledger_entry"]["artifact_version"] = bad_value
    elif attribute == "requester_authentication_version":
        receipt_document["requester_authentication_ref"][
            "artifact_version"
        ] = bad_value
    elif attribute == "signature_verification_version":
        receipt_document["principal_verifications"][0][
            "signature_verification_ref"
        ]["artifact_version"] = bad_value
    else:
        receipt_document["ledger_head_sha256"] = bad_value
    assert validate_artifact_mapping(receipt_document).ok is False

    setattr(ledger, attribute, bad_value)
    with pytest.raises(AuthorityContractError) as predispatch:
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
    assert predispatch.value.reason_code == reason_code


def test_standing_grant_evidence_roles_are_exact_in_code_and_schema() -> None:
    request = _request()
    valid = _standing_grant(request)
    assert validate_standing_authorization(valid) == valid

    for field, reference in (
        (
            "ledger_record",
            replace(valid.ledger_record, artifact_version="brief/1.0"),
        ),
        (
            "signature_verification_refs",
            (
                replace(
                    valid.signature_verification_refs[0],
                    artifact_version="brief/1.0",
                ),
            ),
        ),
    ):
        invalid = _rehash_standing_grant(valid, **{field: reference})
        with pytest.raises(AuthorityContractError) as rejected:
            validate_standing_authorization(invalid)
        assert rejected.value.reason_code == "authority.evidence.role"

        document = standing_authorization_to_mapping(valid)
        if field == "ledger_record":
            document["ledger_record"]["artifact_version"] = "brief/1.0"
        else:
            document["signature_verification_refs"][0][
                "artifact_version"
            ] = "brief/1.0"
        assert validate_artifact_mapping(document).ok is False


def test_r4_receipts_enforce_target_short_expiry_initial_and_predispatch() -> None:
    request = _r4_request()
    references = (
        _ref("ledger/human-a.json", "7", "human-approval/1.0"),
        _ref("ledger/human-b.json", "8", "human-approval/1.0"),
    )
    too_long = FakeLedger(
        AuthoritySource.DUAL_HUMAN,
        ("human-a", "human-b"),
    )
    too_long.validity_seconds = 301
    with pytest.raises(AuthorityContractError) as initial:
        evaluate_authority(
            request,
            target_policy_bundle(),
            ledger=too_long,
            authority_references=references,
            evaluated_at=NOW,
        )
    assert initial.value.reason_code == "authority.r4.expiry_too_long"

    ledger = FakeLedger(
        AuthoritySource.DUAL_HUMAN,
        ("human-a", "human-b"),
    )
    ledger.validity_seconds = 300
    _, decision = evaluate_authority(
        request,
        target_policy_bundle(),
        ledger=ledger,
        authority_references=references,
        evaluated_at=NOW,
    )
    assert decision.status is AuthorityDecisionStatus.AUTHORIZED
    ledger.validity_seconds = 301
    with pytest.raises(AuthorityContractError) as predispatch:
        revalidate_authority_for_side_effect(
            decision,
            request,
            ledger=ledger,
            current_context=request.gate_context,
            workspace_observation_sha256="6" * 64,
            adapter_id="executor-a",
            service_identity="service-a",
            evaluated_at=NOW + timedelta(seconds=1),
            purpose=VerificationPurpose.MUTATION,
        )
    assert predispatch.value.reason_code == "authority.r4.expiry_too_long"


@pytest.mark.parametrize(
    ("source", "principals"),
    (
        (AuthoritySource.POLICY, ()),
        (AuthoritySource.ONE_SHOT_HUMAN, ("human-b",)),
    ),
)
def test_predispatch_cannot_rebind_the_initial_authority_basis(
    source: AuthoritySource,
    principals: tuple[str, ...],
) -> None:
    request = _request()
    ledger = FakeLedger(AuthoritySource.ONE_SHOT_HUMAN, ("human-a",))
    _, decision = evaluate_authority(
        request,
        target_policy_bundle(),
        ledger=ledger,
        authority_references=(
            _ref("ledger/human-a.json", "5", "human-approval/1.0"),
        ),
        evaluated_at=NOW,
    )
    ledger.source = source
    ledger.principals = principals
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


@pytest.mark.parametrize(
    "shared_flag",
    ("shared_human_signature", "shared_human_signature_bytes"),
)
def test_two_humans_cannot_share_one_signature_verification(
    shared_flag: str,
) -> None:
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
        request_id="mutation-shared-signature",
        request_envelope_sha256="6" * 64,
        idempotency_key="mutation-shared-signature",
        requester_principal_id="mutation-requester",
        action_id="managed_mutation",
        capability_id="managed_mutation",
        executable_plan_sha256="5" * 64,
        action_risk=ActionRisk.R4,
        authority_requirement=AuthorityRequirement.TWO_INDEPENDENT_HUMANS,
        side_effect=True,
        gate_context=context,
        profiles=ProfileSelection(
            AssuranceProfile.HIGH_ASSURANCE,
            AutonomyProfile.ASSISTED,
        ),
        scope=_scope(),
        hard_escalation_facts=_hard_escalation_facts(),
    )
    ledger = FakeLedger(AuthoritySource.DUAL_HUMAN, ("human-a", "human-b"))
    setattr(ledger, shared_flag, True)
    with pytest.raises(AuthorityContractError, match="distinct bytes"):
        evaluate_authority(
            request,
            policy,
            ledger=ledger,
            authority_references=(
                _ref("ledger/human-a.json", "7", "human-approval/1.0"),
                _ref("ledger/human-b.json", "8", "human-approval/1.0"),
            ),
            evaluated_at=NOW,
        )


def test_initial_receipt_cannot_rebind_authenticated_requester() -> None:
    request = _request()
    ledger = FakeLedger(AuthoritySource.ONE_SHOT_HUMAN, ("human-a",))
    ledger.requester_principal_override = "requester-other"
    with pytest.raises(AuthorityContractError, match="another request"):
        evaluate_authority(
            request,
            target_policy_bundle(),
            ledger=ledger,
            authority_references=(
                _ref("ledger/human-a.json", "5", "human-approval/1.0"),
            ),
            evaluated_at=NOW,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("risk_assessment_override", "8" * 64),
        ("requester_authentication_digest", "8"),
        ("workflow_evaluation_override", "8" * 64),
        ("workflow_verification_digest", "8"),
    ),
)
def test_predispatch_cannot_rebind_risk_requester_or_workflow_proof(
    field: str,
    value: str,
) -> None:
    request = _request()
    ledger = FakeLedger(AuthoritySource.ONE_SHOT_HUMAN, ("human-a",))
    _, decision = evaluate_authority(
        request,
        target_policy_bundle(),
        ledger=ledger,
        authority_references=(
            _ref("ledger/human-a.json", "5", "human-approval/1.0"),
        ),
        evaluated_at=NOW,
    )
    setattr(ledger, field, value)
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


@pytest.mark.parametrize(
    ("field", "value", "expected_reason"),
    (
        (
            "workflow_evaluation_override",
            "8" * 64,
            "authority.receipt.rebound",
        ),
        (
            "omit_workflow_verification",
            True,
            "authority.receipt.workflow_evaluation",
        ),
        (
            "workflow_verification_digest",
            "9",
            "authority.receipt.signature_content",
        ),
    ),
)
def test_initial_receipt_requires_exact_trusted_workflow_evaluation_proof(
    field: str,
    value: object,
    expected_reason: str,
) -> None:
    request = _request()
    ledger = FakeLedger(AuthoritySource.ONE_SHOT_HUMAN, ("human-a",))
    setattr(ledger, field, value)
    _assert_authority_error(
        expected_reason,
        lambda: evaluate_authority(
            request,
            target_policy_bundle(),
            ledger=ledger,
            authority_references=(
                _ref("ledger/human-a.json", "5", "human-approval/1.0"),
            ),
            evaluated_at=NOW,
        ),
    )


def test_trusted_ledger_rejects_unrecognized_workflow_evaluation() -> None:
    request = _request()
    ledger = FakeLedger(AuthoritySource.ONE_SHOT_HUMAN, ("human-a",))
    ledger.trusted_workflow_evaluation_sha256 = "8" * 64
    _, decision = evaluate_authority(
        request,
        target_policy_bundle(),
        ledger=ledger,
        authority_references=(
            _ref("ledger/human-a.json", "5", "human-approval/1.0"),
        ),
        evaluated_at=NOW,
    )
    assert decision.status is AuthorityDecisionStatus.DENIED
    assert decision.reason_codes == ("authority.ledger.denied",)


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


def test_authority_scope_requires_canonical_input_and_output_order() -> None:
    with pytest.raises(AuthorityContractError, match="identity order"):
        _request(
            scope=_scope(
                inputs=(
                    _ref("inputs/z.json", "b"),
                    _ref("inputs/a.json", "a"),
                )
            )
        )
    with pytest.raises(AuthorityContractError, match="path order"):
        _request(
            scope=replace(
                _scope(),
                allowed_outputs=(
                    OutputScope("outputs/z", ("media-output/1.0",)),
                    OutputScope("outputs/a", ("media-output/1.0",)),
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
        requester_principal_id="mutation-requester",
        action_id="managed_mutation",
        capability_id="managed_mutation",
        executable_plan_sha256="5" * 64,
        action_risk=ActionRisk.R4,
        authority_requirement=AuthorityRequirement.TWO_INDEPENDENT_HUMANS,
        side_effect=True,
        gate_context=context,
        profiles=ProfileSelection(AssuranceProfile.HIGH_ASSURANCE, AutonomyProfile.ASSISTED),
        scope=_scope(),
        hard_escalation_facts=_hard_escalation_facts(),
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
