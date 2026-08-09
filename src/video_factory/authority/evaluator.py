"""Fail-closed authority evaluation and pre-side-effect revalidation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
import unicodedata

from video_factory.approvals import GateContext, gate_context_sha256, gate_context_to_mapping
from video_factory.config import canonical_sha256
from video_factory.domain import ArtifactReference, HashDigest, IdempotencyKey, OpaqueId
from video_factory.json_boundary import parse_rfc3339_datetime
from video_factory.workflow import (
    ActionRisk,
    AssuranceProfile,
    AuthorityRequirement,
    AutonomyProfile,
    ExecutableProductionPlan,
    WorkflowEvaluation,
    default_workflow_definition,
    validate_executable_production_plan,
)

from .contracts import (
    ActionAuthorityRequest,
    ActionRiskAssessment,
    ApprovalRequest,
    AuthorityContractError,
    AuthorityDecision,
    AuthorityDecisionStatus,
    AuthorityScope,
    AuthoritySource,
    AuthorityVerificationReceipt,
    HardEscalationFact,
    HardEscalationState,
    LedgerRecordState,
    OutputScope,
    PrincipalSignatureVerification,
    ProfileSelection,
    TrustedAuthorizationLedger,
    UnverifiedStandingAuthorization,
    VerificationPurpose,
    VerifiedAuthorityDecision,
)
from .policy import (
    PolicyBundle,
    classify_action_risk,
    require_target_policy_bundle,
    target_policy_bundle,
    validate_risk_assessment,
)


AUTHORITY_DECISION_VERSION = "authority-decision/1.0"
APPROVAL_REQUEST_VERSION = "approval-request/1.0"
STANDING_AUTHORIZATION_VERSION = "standing-authorization/1.0"
VERIFICATION_RECEIPT_VERSION = "authority-verification-receipt/1.0"
LEDGER_ENTRY_VERSION = "authority-ledger-entry/1.0"
PRINCIPAL_AUTHENTICATION_VERSION = "principal-authentication/1.0"
SIGNATURE_VERIFICATION_VERSION = "signature-verification/1.0"
WORKFLOW_EVALUATION_VERIFICATION_VERSION = (
    "workflow-evaluation-verification/1.0"
)


_ASSURANCE_RANK = {
    AssuranceProfile.DRAFT: 0,
    AssuranceProfile.PRODUCTION: 1,
    AssuranceProfile.HIGH_ASSURANCE: 2,
}
_AUTONOMY_RANK = {
    AutonomyProfile.OBSERVE_ONLY: 0,
    AutonomyProfile.ASSISTED: 1,
    AutonomyProfile.GUARDED_AUTONOMOUS: 2,
    AutonomyProfile.BOUNDED_AUTONOMOUS: 3,
}


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise AuthorityContractError("authority.time.naive", f"{label} must be timezone aware")


def _canonical_path(path: str) -> str:
    if not path or path != unicodedata.normalize("NFC", path) or "\\" in path:
        raise AuthorityContractError("authority.scope.path", "artifact path is not canonical NFC POSIX")
    if path.startswith("/") or path.endswith("/") or ":" in path.split("/", 1)[0]:
        raise AuthorityContractError("authority.scope.path", "artifact path must be relative")
    parts = path.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise AuthorityContractError("authority.scope.path", "artifact path contains an empty or dot segment")
    if any(unicodedata.category(character) == "Cc" for character in path):
        raise AuthorityContractError("authority.scope.path", "artifact path contains a control character")
    reserved = {
        "con", "prn", "aux", "nul", "clock$", "conin$", "conout$",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
    if any(
        ":" in part
        or part.endswith((".", " "))
        or part.split(".", 1)[0].casefold() in reserved
        for part in parts
    ):
        raise AuthorityContractError(
            "authority.scope.path",
            "artifact path is unsafe on supported target platforms",
        )
    return path


def _require_sha256(value: object, label: str) -> str:
    digest = str(value)
    if (
        len(digest) != 64
        or digest.lower() != digest
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise AuthorityContractError(
            "authority.scope.digest",
            f"{label} must be a lowercase SHA-256 digest",
        )
    return digest


def _reference_mapping(reference: ArtifactReference) -> dict[str, str]:
    path = _canonical_path(str(reference.path))
    digest = _require_sha256(reference.sha256, "artifact digest")
    version = str(reference.artifact_version)
    if "/" not in version or not version.strip() or version.strip() != version:
        raise AuthorityContractError("authority.scope.version", "artifact version is invalid")
    return {"path": path, "sha256": digest, "artifact_version": version}


def _require_reference_version(
    reference: ArtifactReference,
    expected_version: str,
    label: str,
) -> None:
    _reference_mapping(reference)
    if str(reference.artifact_version) != expected_version:
        raise AuthorityContractError(
            "authority.evidence.role",
            f"{label} must use {expected_version}",
        )


def _principal_verification_mapping(
    verification: PrincipalSignatureVerification,
) -> dict[str, object]:
    return {
        "principal_id": _token(str(verification.principal_id), "principal_id"),
        "signature_verification_ref": _reference_mapping(
            verification.signature_verification_ref
        ),
    }


def _validate_reference_set(
    references: Sequence[ArtifactReference],
    *,
    require_canonical_order: bool = False,
) -> tuple[ArtifactReference, ...]:
    identities: dict[str, tuple[str, str, str]] = {}
    ordered: list[tuple[str, str, str, str]] = []
    for reference in references:
        mapping = _reference_mapping(reference)
        key = unicodedata.normalize("NFC", mapping["path"]).casefold()
        identity = (mapping["path"], mapping["sha256"], mapping["artifact_version"])
        existing = identities.get(key)
        if existing is not None and existing != identity:
            raise AuthorityContractError("authority.scope.path_collision", "artifact path identities collide")
        identities[key] = identity
        ordered.append((key, *identity))
    if len(references) != len(identities):
        raise AuthorityContractError("authority.scope.reference_duplicate", "artifact references are duplicated")
    if require_canonical_order and ordered != sorted(ordered):
        raise AuthorityContractError(
            "authority.scope.reference_order",
            "artifact references are not in canonical identity order",
        )
    return tuple(references)


def _hard_escalation_fact_mappings(
    facts: Sequence[HardEscalationFact],
) -> list[dict[str, object]]:
    mappings: list[dict[str, object]] = []
    all_evidence: list[ArtifactReference] = []
    for fact in facts:
        trigger = _token(fact.trigger, "hard escalation trigger")
        if not isinstance(fact.state, HardEscalationState):
            raise AuthorityContractError(
                "authority.escalation.state",
                "hard escalation state is outside the closed tri-state",
            )
        if not fact.evidence_refs:
            raise AuthorityContractError(
                "authority.escalation.evidence",
                "every hard escalation fact requires exact evidence",
            )
        _validate_reference_set(
            fact.evidence_refs, require_canonical_order=True
        )
        all_evidence.extend(fact.evidence_refs)
        mappings.append(
            {
                "trigger": trigger,
                "state": fact.state.value,
                "evidence_refs": [
                    _reference_mapping(value) for value in fact.evidence_refs
                ],
            }
        )
    _validate_reference_set(tuple(all_evidence))
    return mappings


def _token(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or value != unicodedata.normalize("NFC", value)
        or any(unicodedata.category(character) == "Cc" for character in value)
    ):
        raise AuthorityContractError(
            "authority.scope.token",
            f"{label} is not a canonical non-empty token",
        )
    return value


def _output_scope_mappings(
    values: Sequence[OutputScope],
) -> list[dict[str, object]]:
    output_keys: set[str] = set()
    outputs: list[dict[str, object]] = []
    for item in values:
        path = _canonical_path(item.path_prefix)
        key = unicodedata.normalize("NFC", path).casefold()
        versions = tuple(sorted(set(item.artifact_versions)))
        if (
            not versions
            or versions != item.artifact_versions
            or any(
                key == existing
                or key.startswith(existing + "/")
                or existing.startswith(key + "/")
                for existing in output_keys
            )
            or any("/" not in _token(version, "output artifact version") for version in versions)
        ):
            raise AuthorityContractError(
                "authority.scope.output",
                "output scope is not canonical, sorted and unique",
            )
        output_keys.add(key)
        outputs.append({"path_prefix": path, "artifact_versions": list(versions)})
    if [str(item["path_prefix"]).casefold() for item in outputs] != sorted(
        str(item["path_prefix"]).casefold() for item in outputs
    ):
        raise AuthorityContractError(
            "authority.scope.output_order",
            "output scopes are not in canonical path order",
        )
    return outputs


def _scope_mapping(scope: AuthorityScope) -> dict[str, object]:
    if min(scope.cost_minor_units, scope.candidate_count, scope.retry_index) < 0:
        raise AuthorityContractError("authority.scope.number", "scope counters cannot be negative")
    if not scope.currency or scope.currency.upper() != scope.currency or len(scope.currency) != 3:
        raise AuthorityContractError("authority.scope.currency", "currency must be a three-letter uppercase code")
    for label, value in (
        ("workspace_id", scope.workspace_id),
        ("channel_id", scope.channel_id),
        ("concept_id", scope.concept_id),
        ("episode_id", scope.episode_id),
    ):
        _token(str(value), label)
    for label, value in (
        ("provider_id", scope.provider_id),
        ("model_id", scope.model_id),
        ("destination", scope.destination),
    ):
        if value is not None:
            _token(str(value), label)
    _validate_reference_set(
        scope.input_artifacts, require_canonical_order=True
    )
    outputs = _output_scope_mappings(scope.allowed_outputs)
    return {
        "workspace_id": str(scope.workspace_id),
        "channel_id": str(scope.channel_id),
        "concept_id": str(scope.concept_id),
        "episode_id": str(scope.episode_id),
        "provider_id": str(scope.provider_id) if scope.provider_id is not None else None,
        "model_id": str(scope.model_id) if scope.model_id is not None else None,
        "destination": scope.destination,
        "cost_minor_units": scope.cost_minor_units,
        "currency": scope.currency,
        "candidate_count": scope.candidate_count,
        "retry_index": scope.retry_index,
        "input_artifacts": [_reference_mapping(value) for value in scope.input_artifacts],
        "allowed_outputs": outputs,
    }


def _request_identity(
    request_id: str,
    request_envelope_sha256: str,
    idempotency_key: str,
    requester_principal_id: str,
    action_id: str,
    capability_id: str,
    executable_plan_sha256: str,
    workflow_evaluation_sha256: str | None,
    workflow_evaluation_predecessor_sha256s: Sequence[str],
    action_risk: ActionRisk,
    authority_requirement: AuthorityRequirement,
    side_effect: bool,
    gate_context: GateContext,
    profiles: ProfileSelection,
    scope: AuthorityScope,
    hard_escalation_facts: Sequence[HardEscalationFact],
) -> dict[str, object]:
    return {
        "request_id": request_id,
        "request_envelope_sha256": request_envelope_sha256,
        "idempotency_key": idempotency_key,
        "requester_principal_id": requester_principal_id,
        "action_id": action_id,
        "capability_id": capability_id,
        "executable_plan_sha256": executable_plan_sha256,
        "workflow_evaluation_sha256": workflow_evaluation_sha256,
        "workflow_evaluation_predecessor_sha256s": list(
            workflow_evaluation_predecessor_sha256s
        ),
        "action_risk": action_risk.value,
        "authority_requirement": authority_requirement.value,
        "side_effect": side_effect,
        "gate_context": gate_context_to_mapping(gate_context),
        "profiles": {
            "assurance": profiles.assurance.value,
            "autonomy": profiles.autonomy.value,
        },
        "scope": _scope_mapping(scope),
        "hard_escalation_facts": _hard_escalation_fact_mappings(
            hard_escalation_facts
        ),
    }


def action_authority_request_to_mapping(request: ActionAuthorityRequest) -> dict[str, object]:
    validate_action_authority_request(request)
    return {
        **_request_identity(
            str(request.request_id),
            str(request.request_envelope_sha256),
            str(request.idempotency_key),
            str(request.requester_principal_id),
            str(request.action_id),
            str(request.capability_id),
            str(request.executable_plan_sha256),
            (
                str(request.workflow_evaluation.evaluation_sha256)
                if request.workflow_evaluation is not None
                else None
            ),
            tuple(
                str(value.evaluation_sha256)
                for value in request.workflow_evaluation_predecessors
            ),
            request.action_risk,
            request.authority_requirement,
            request.side_effect,
            request.gate_context,
            request.profiles,
            request.scope,
            request.hard_escalation_facts,
        ),
        "request_sha256": str(request.request_sha256),
    }


def build_action_authority_request(
    *,
    request_id: str,
    request_envelope_sha256: str,
    idempotency_key: str,
    requester_principal_id: str,
    plan: ExecutableProductionPlan,
    workflow_evaluation: WorkflowEvaluation,
    workflow_evaluation_predecessors: Sequence[WorkflowEvaluation] = (),
    profiles: ProfileSelection,
    scope: AuthorityScope,
    hard_escalation_facts: Sequence[HardEscalationFact],
) -> ActionAuthorityRequest:
    predecessors = tuple(workflow_evaluation_predecessors)
    validate_executable_production_plan(
        default_workflow_definition(),
        plan,
        workflow_evaluation,
        predecessors=predecessors,
    )
    identity = _request_identity(
        request_id,
        request_envelope_sha256,
        idempotency_key,
        requester_principal_id,
        str(plan.action_id),
        str(plan.capability_id),
        str(plan.plan_sha256),
        str(workflow_evaluation.evaluation_sha256),
        tuple(str(value.evaluation_sha256) for value in predecessors),
        plan.risk,
        plan.authority_requirement,
        plan.side_effect,
        plan.gate_context,
        profiles,
        scope,
        hard_escalation_facts,
    )
    request = ActionAuthorityRequest(
        request_id=OpaqueId(request_id),
        request_sha256=canonical_sha256(identity),
        request_envelope_sha256=HashDigest(request_envelope_sha256),
        idempotency_key=IdempotencyKey(idempotency_key),
        requester_principal_id=OpaqueId(requester_principal_id),
        action_id=plan.action_id,
        capability_id=plan.capability_id,
        executable_plan_sha256=plan.plan_sha256,
        action_risk=plan.risk,
        authority_requirement=plan.authority_requirement,
        side_effect=plan.side_effect,
        plan=plan,
        workflow_evaluation=workflow_evaluation,
        workflow_evaluation_predecessors=predecessors,
        gate_context=plan.gate_context,
        profiles=profiles,
        scope=scope,
        hard_escalation_facts=tuple(hard_escalation_facts),
    )
    return validate_action_authority_request(request)


def build_bound_action_authority_request(
    *,
    request_id: str,
    request_envelope_sha256: str,
    idempotency_key: str,
    requester_principal_id: str,
    action_id: str,
    capability_id: str,
    executable_plan_sha256: str,
    action_risk: ActionRisk,
    authority_requirement: AuthorityRequirement,
    side_effect: bool,
    gate_context: GateContext,
    profiles: ProfileSelection,
    scope: AuthorityScope,
    hard_escalation_facts: Sequence[HardEscalationFact],
) -> ActionAuthorityRequest:
    """Build a non-workflow request; target policy still recomputes its risk.

    This is the narrow bridge used by the existing W02 managed-mutation plan.
    It does not accept a grant, approval, or authority boolean.
    """

    if str(gate_context.executable_plan_sha256) != executable_plan_sha256:
        raise AuthorityContractError("authority.request.context", "GateContext is bound to another plan")
    identity = _request_identity(
        request_id, request_envelope_sha256, idempotency_key,
        requester_principal_id, action_id,
        capability_id, executable_plan_sha256, None, (), action_risk,
        authority_requirement, side_effect, gate_context, profiles, scope,
        hard_escalation_facts,
    )
    return validate_action_authority_request(
        ActionAuthorityRequest(
            request_id=OpaqueId(request_id),
            request_sha256=canonical_sha256(identity),
            request_envelope_sha256=HashDigest(request_envelope_sha256),
            idempotency_key=IdempotencyKey(idempotency_key),
            requester_principal_id=OpaqueId(requester_principal_id),
            action_id=OpaqueId(action_id),
            capability_id=OpaqueId(capability_id),
            executable_plan_sha256=HashDigest(executable_plan_sha256),
            action_risk=action_risk,
            authority_requirement=authority_requirement,
            side_effect=side_effect,
            plan=None,
            workflow_evaluation=None,
            workflow_evaluation_predecessors=(),
            gate_context=gate_context,
            profiles=profiles,
            scope=scope,
            hard_escalation_facts=tuple(hard_escalation_facts),
        )
    )


def validate_action_authority_request(request: ActionAuthorityRequest) -> ActionAuthorityRequest:
    if request.plan is not None:
        if request.workflow_evaluation is None:
            raise AuthorityContractError(
                "authority.request.evaluation",
                "workflow authority requires the exact clean evaluation",
            )
        validate_executable_production_plan(
            default_workflow_definition(),
            request.plan,
            request.workflow_evaluation,
            predecessors=request.workflow_evaluation_predecessors,
        )
        if (
            request.gate_context != request.plan.gate_context
            or request.action_id != request.plan.action_id
            or request.capability_id != request.plan.capability_id
            or request.executable_plan_sha256 != request.plan.plan_sha256
            or request.action_risk is not request.plan.risk
            or request.authority_requirement is not request.plan.authority_requirement
            or request.side_effect is not request.plan.side_effect
        ):
            raise AuthorityContractError("authority.request.plan_rebound", "request fields differ from the workflow plan")
    elif (
        request.workflow_evaluation is not None
        or request.workflow_evaluation_predecessors
        or (
        request.action_id != "managed_mutation"
        or request.capability_id != "managed_mutation"
        or request.action_risk is not ActionRisk.R4
        or request.authority_requirement is not AuthorityRequirement.TWO_INDEPENDENT_HUMANS
        or request.side_effect is not True
        )
    ):
        raise AuthorityContractError("authority.request.unowned_action", "non-workflow action is not the W02 R4 bridge")
    if request.gate_context.executable_plan_sha256 != request.executable_plan_sha256:
        raise AuthorityContractError("authority.request.context", "request context does not match the plan digest")
    expected_triggers = target_policy_bundle().hard_escalation_triggers
    actual_triggers = tuple(
        value.trigger for value in request.hard_escalation_facts
    )
    if actual_triggers != expected_triggers:
        raise AuthorityContractError(
            "authority.escalation.coverage",
            "hard escalation facts must cover the exact target policy order",
        )
    _hard_escalation_fact_mappings(request.hard_escalation_facts)
    _validate_reference_set(
        (
            *request.scope.input_artifacts,
            *(
                reference
                for fact in request.hard_escalation_facts
                for reference in fact.evidence_refs
            ),
        )
    )
    if (
        request.plan is not None
        and request.side_effect
        and any(
            value is None
            for value in (
                request.scope.provider_id,
                request.scope.model_id,
                request.scope.destination,
            )
        )
    ):
        raise AuthorityContractError(
            "authority.scope.production_missing",
            "workflow side effects require provider, model and destination",
        )
    for label, value in (
        ("request_id", request.request_id),
        ("idempotency_key", request.idempotency_key),
        ("requester_principal_id", request.requester_principal_id),
        ("action_id", request.action_id),
        ("capability_id", request.capability_id),
    ):
        _token(str(value), label)
    envelope = str(request.request_envelope_sha256)
    if len(envelope) != 64 or any(value not in "0123456789abcdef" for value in envelope):
        raise AuthorityContractError("authority.request.envelope", "request envelope digest is invalid")
    identity = _request_identity(
        str(request.request_id), envelope, str(request.idempotency_key),
        str(request.requester_principal_id),
        str(request.action_id), str(request.capability_id),
        str(request.executable_plan_sha256),
        (
            str(request.workflow_evaluation.evaluation_sha256)
            if request.workflow_evaluation is not None
            else None
        ),
        tuple(
            str(value.evaluation_sha256)
            for value in request.workflow_evaluation_predecessors
        ),
        request.action_risk,
        request.authority_requirement, request.side_effect, request.gate_context,
        request.profiles, request.scope,
        request.hard_escalation_facts,
    )
    if str(canonical_sha256(identity)) != str(request.request_sha256):
        raise AuthorityContractError("authority.request.digest", "authority request digest mismatch")
    return request


def _standing_identity(value: UnverifiedStandingAuthorization) -> dict[str, object]:
    return {
        "artifact_version": value.artifact_version,
        "gate_context_sha256": str(value.gate_context_sha256),
        "capability_ids": [str(item) for item in value.capability_ids],
        "workspace_ids": [str(item) for item in value.workspace_ids],
        "channel_ids": [str(item) for item in value.channel_ids],
        "concept_ids": [str(item) for item in value.concept_ids],
        "episode_ids": [str(item) for item in value.episode_ids],
        "provider_ids": [str(item) for item in value.provider_ids],
        "model_ids": [str(item) for item in value.model_ids],
        "destinations": list(value.destinations),
        "max_cost_per_run_minor": value.max_cost_per_run_minor,
        "max_cost_per_day_minor": value.max_cost_per_day_minor,
        "currency": value.currency,
        "max_candidates": value.max_candidates,
        "max_retries": value.max_retries,
        "allowed_risks": [item.value for item in value.allowed_risks],
        "minimum_assurance": value.minimum_assurance.value,
        "maximum_autonomy": value.maximum_autonomy.value,
        "exact_input_artifacts": [
            _reference_mapping(item) for item in value.exact_input_artifacts
        ],
        "allowed_outputs": _output_scope_mappings(value.allowed_outputs),
        "valid_from": value.valid_from,
        "expires_at": value.expires_at,
        "ledger_record": _reference_mapping(value.ledger_record),
        "signature_verification_refs": [
            _reference_mapping(item) for item in value.signature_verification_refs
        ],
        "authority_effect": value.authority_effect,
    }


def standing_authorization_to_mapping(value: UnverifiedStandingAuthorization) -> dict[str, object]:
    validate_standing_authorization(value)
    return {
        **_standing_identity(value),
        "authorization_id": str(value.authorization_id),
        "authorization_sha256": str(value.authorization_sha256),
    }


def standing_authorization_sha256(value: UnverifiedStandingAuthorization) -> HashDigest:
    """Compute structural grant identity; this does not verify issuer or ledger."""

    return canonical_sha256(_standing_identity(value))


def validate_standing_authorization(value: UnverifiedStandingAuthorization) -> UnverifiedStandingAuthorization:
    if value.artifact_version != STANDING_AUTHORIZATION_VERSION or value.authority_effect != "none":
        raise AuthorityContractError("authority.grant.contract", "standing authorization is parse-only")
    collections = (
        value.capability_ids, value.workspace_ids, value.channel_ids, value.concept_ids,
        value.episode_ids, value.provider_ids, value.model_ids, value.destinations,
        value.allowed_risks,
    )
    if any(tuple(items) != tuple(sorted(set(items), key=str)) for items in collections):
        raise AuthorityContractError("authority.grant.scope_order", "standing scope is not sorted and unique")
    if not value.capability_ids or not value.allowed_risks:
        raise AuthorityContractError("authority.grant.scope", "standing authorization scope is empty")
    for label, items in (
        ("capability_id", value.capability_ids),
        ("workspace_id", value.workspace_ids),
        ("channel_id", value.channel_ids),
        ("concept_id", value.concept_ids),
        ("episode_id", value.episode_ids),
        ("provider_id", value.provider_ids),
        ("model_id", value.model_ids),
        ("destination", value.destinations),
    ):
        for item in items:
            _token(str(item), label)
    if ActionRisk.R4 in value.allowed_risks:
        raise AuthorityContractError("authority.r4.standing_forbidden", "R4 cannot use a standing authorization")
    if min(
        value.max_cost_per_run_minor, value.max_cost_per_day_minor,
        value.max_candidates, value.max_retries,
    ) < 0:
        raise AuthorityContractError("authority.grant.limit", "standing authorization limit is negative")
    valid_from = parse_rfc3339_datetime(value.valid_from)
    expires_at = parse_rfc3339_datetime(value.expires_at)
    if valid_from >= expires_at:
        raise AuthorityContractError("authority.grant.window", "standing authorization window is empty")
    _validate_reference_set(
        value.exact_input_artifacts, require_canonical_order=True
    )
    _output_scope_mappings(value.allowed_outputs)
    if not value.signature_verification_refs:
        raise AuthorityContractError(
            "authority.grant.signature",
            "standing authorization requires signature verification evidence",
        )
    _validate_reference_set(
        value.signature_verification_refs,
        require_canonical_order=True,
    )
    _require_reference_version(
        value.ledger_record,
        LEDGER_ENTRY_VERSION,
        "standing authorization ledger record",
    )
    for reference in value.signature_verification_refs:
        _require_reference_version(
            reference,
            SIGNATURE_VERIFICATION_VERSION,
            "standing authorization signature verification",
        )
    _validate_reference_set((value.ledger_record, *value.signature_verification_refs))
    digest = standing_authorization_sha256(value)
    if str(digest) != str(value.authorization_sha256):
        raise AuthorityContractError("authority.grant.digest", "standing authorization digest mismatch")
    if str(value.authorization_id) != f"standing-authorization-{str(digest)[:20]}":
        raise AuthorityContractError("authority.grant.identity", "standing authorization id mismatch")
    return value


def _standing_scope_blockers(
    grant: UnverifiedStandingAuthorization,
    request: ActionAuthorityRequest,
    risk: ActionRiskAssessment,
    evaluated_at: datetime,
) -> tuple[str, ...]:
    validate_standing_authorization(grant)
    blockers: list[str] = []
    scope = request.scope
    context_digest = str(gate_context_sha256(request.gate_context))
    if str(grant.gate_context_sha256) != context_digest:
        blockers.append("authority.context.mismatch")
    checks = (
        (request.capability_id, grant.capability_ids, "authority.scope.capability"),
        (scope.workspace_id, grant.workspace_ids, "authority.scope.workspace"),
        (scope.channel_id, grant.channel_ids, "authority.scope.channel"),
        (scope.concept_id, grant.concept_ids, "authority.scope.concept"),
        (scope.episode_id, grant.episode_ids, "authority.scope.episode"),
    )
    for actual, allowed, reason in checks:
        if actual not in allowed:
            blockers.append(reason)
    optional_checks = (
        (scope.provider_id, grant.provider_ids, "authority.scope.provider"),
        (scope.model_id, grant.model_ids, "authority.scope.model"),
        (scope.destination, grant.destinations, "authority.scope.destination"),
    )
    for actual, allowed, reason in optional_checks:
        if (
            actual is None
            or (actual is not None and actual not in allowed)
        ):
            blockers.append(reason)
    if scope.cost_minor_units > grant.max_cost_per_run_minor or scope.currency != grant.currency:
        blockers.append("authority.scope.cost_run")
    if scope.candidate_count > grant.max_candidates:
        blockers.append("authority.scope.candidates")
    if scope.retry_index > grant.max_retries:
        blockers.append("authority.scope.retries")
    if risk.effective_risk not in grant.allowed_risks:
        blockers.append("authority.scope.risk")
    if _ASSURANCE_RANK[request.profiles.assurance] < _ASSURANCE_RANK[grant.minimum_assurance]:
        blockers.append("authority.scope.assurance")
    if _AUTONOMY_RANK[request.profiles.autonomy] > _AUTONOMY_RANK[grant.maximum_autonomy]:
        blockers.append("authority.scope.autonomy")
    request_inputs = tuple(
        _reference_mapping(item) for item in scope.input_artifacts
    )
    grant_inputs = tuple(
        _reference_mapping(item) for item in grant.exact_input_artifacts
    )
    if request_inputs != grant_inputs:
        blockers.append("authority.scope.inputs")
    if _output_scope_mappings(scope.allowed_outputs) != _output_scope_mappings(
        grant.allowed_outputs
    ):
        blockers.append("authority.scope.outputs")
    if evaluated_at < parse_rfc3339_datetime(grant.valid_from):
        blockers.append("authority.grant.not_yet_valid")
    if evaluated_at >= parse_rfc3339_datetime(grant.expires_at):
        blockers.append("authority.grant.expired")
    return tuple(dict.fromkeys(blockers))


def _receipt_identity(receipt: AuthorityVerificationReceipt) -> dict[str, object]:
    return {
        "artifact_version": receipt.artifact_version,
        "purpose": receipt.purpose.value,
        "action_request_sha256": str(receipt.action_request_sha256),
        "authority_decision_sha256": (
            str(receipt.authority_decision_sha256)
            if receipt.authority_decision_sha256 is not None else None
        ),
        "gate_context_sha256": str(receipt.gate_context_sha256),
        "risk_assessment_sha256": str(receipt.risk_assessment_sha256),
        "workflow_evaluation_sha256": (
            str(receipt.workflow_evaluation_sha256)
            if receipt.workflow_evaluation_sha256 is not None
            else None
        ),
        "workflow_evaluation_verification_ref": (
            _reference_mapping(
                receipt.workflow_evaluation_verification_ref
            )
            if receipt.workflow_evaluation_verification_ref is not None
            else None
        ),
        "authority_source": receipt.authority_source.value,
        "ledger_state": receipt.ledger_state.value,
        "ledger_head_sha256": str(receipt.ledger_head_sha256),
        "ledger_entry": _reference_mapping(receipt.ledger_entry),
        "requester_principal_id": str(receipt.requester_principal_id),
        "requester_authentication_ref": _reference_mapping(
            receipt.requester_authentication_ref
        ),
        "grant_sha256": str(receipt.grant_sha256) if receipt.grant_sha256 is not None else None,
        "principal_verifications": [
            _principal_verification_mapping(item)
            for item in receipt.principal_verifications
        ],
        "signature_verification_refs": [
            _reference_mapping(item) for item in receipt.signature_verification_refs
        ],
        "revocation_checked_at": receipt.revocation_checked_at,
        "kill_switch_clear": receipt.kill_switch_clear,
        "reserved_cost_minor_units": receipt.reserved_cost_minor_units,
        "currency": receipt.currency,
        "reserved_candidates": receipt.reserved_candidates,
        "retry_index": receipt.retry_index,
        "idempotency_key": str(receipt.idempotency_key),
        "workspace_id": str(receipt.workspace_id),
        "workspace_observation_sha256": (
            str(receipt.workspace_observation_sha256)
            if receipt.workspace_observation_sha256 is not None
            else None
        ),
        "adapter_id": str(receipt.adapter_id) if receipt.adapter_id is not None else None,
        "service_identity": str(receipt.service_identity) if receipt.service_identity is not None else None,
        "evaluated_at": receipt.evaluated_at,
        "valid_until": receipt.valid_until,
    }


def authority_verification_receipt_sha256(receipt: AuthorityVerificationReceipt) -> HashDigest:
    """Compute structural receipt identity; this does not authenticate a receipt."""

    return canonical_sha256(_receipt_identity(receipt))


def authority_verification_receipt_to_mapping(receipt: AuthorityVerificationReceipt) -> dict[str, object]:
    validate_authority_verification_receipt(receipt)
    return {
        **_receipt_identity(receipt),
        "receipt_id": str(receipt.receipt_id),
        "receipt_sha256": str(receipt.receipt_sha256),
    }


def _authority_basis_sha256(
    source: AuthoritySource,
    receipt: AuthorityVerificationReceipt | None,
) -> HashDigest:
    if receipt is None:
        raise AuthorityContractError(
            "authority.basis.receipt",
            "execution authority requires a verified authority basis",
        )
    if source is AuthoritySource.POLICY:
        if (
            receipt.grant_sha256 is not None
            or receipt.principal_verifications
            or receipt.signature_verification_refs
        ):
            raise AuthorityContractError(
                "authority.basis.policy",
                "policy authority cannot acquire human or grant evidence",
            )
    return canonical_sha256(
        {
            "authority_source": source.value,
            "grant_sha256": (
                str(receipt.grant_sha256)
                if receipt.grant_sha256 is not None
                else None
            ),
            "ledger_entry": _reference_mapping(receipt.ledger_entry),
            "requester_principal_id": str(receipt.requester_principal_id),
            "requester_authentication_ref": _reference_mapping(
                receipt.requester_authentication_ref
            ),
            "workflow_evaluation_sha256": (
                str(receipt.workflow_evaluation_sha256)
                if receipt.workflow_evaluation_sha256 is not None
                else None
            ),
            "workflow_evaluation_verification_ref": (
                _reference_mapping(
                    receipt.workflow_evaluation_verification_ref
                )
                if receipt.workflow_evaluation_verification_ref is not None
                else None
            ),
            "principal_verifications": [
                _principal_verification_mapping(item)
                for item in receipt.principal_verifications
            ],
            "signature_verification_refs": [
                _reference_mapping(item)
                for item in receipt.signature_verification_refs
            ],
        }
    )


def validate_authority_verification_receipt(
    receipt: AuthorityVerificationReceipt,
) -> AuthorityVerificationReceipt:
    if receipt.artifact_version != VERIFICATION_RECEIPT_VERSION:
        raise AuthorityContractError("authority.receipt.version", "unsupported receipt version")
    if receipt.ledger_state is not LedgerRecordState.ACTIVE:
        raise AuthorityContractError(
            "authority.grant.revoked",
            "receipt ledger state is not current and active",
        )
    if receipt.kill_switch_clear is not True:
        raise AuthorityContractError(
            "authority.kill_switch.engaged",
            "receipt kill switch is engaged",
        )
    _require_sha256(receipt.ledger_head_sha256, "ledger_head_sha256")
    _require_reference_version(
        receipt.ledger_entry,
        LEDGER_ENTRY_VERSION,
        "receipt ledger entry",
    )
    _require_reference_version(
        receipt.requester_authentication_ref,
        PRINCIPAL_AUTHENTICATION_VERSION,
        "requester authentication evidence",
    )
    if min(receipt.reserved_cost_minor_units, receipt.reserved_candidates, receipt.retry_index) < 0:
        raise AuthorityContractError("authority.receipt.limit", "receipt reservation is invalid")
    principal_ids = tuple(
        str(item.principal_id) for item in receipt.principal_verifications
    )
    if principal_ids != tuple(sorted(set(principal_ids))):
        raise AuthorityContractError(
            "authority.receipt.principals",
            "receipt principal verifications are not sorted and unique",
        )
    if receipt.authority_source is AuthoritySource.NONE:
        raise AuthorityContractError(
            "authority.receipt.source",
            "verification receipt must identify its trusted authority source",
        )
    _token(str(receipt.requester_principal_id), "requester_principal_id")
    has_workflow_evaluation = receipt.workflow_evaluation_sha256 is not None
    has_workflow_verification = (
        receipt.workflow_evaluation_verification_ref is not None
    )
    if has_workflow_evaluation != has_workflow_verification:
        raise AuthorityContractError(
            "authority.receipt.workflow_evaluation",
            "workflow evaluation verification binding is partial",
        )
    if (
        receipt.workflow_evaluation_verification_ref is not None
        and str(
            receipt.workflow_evaluation_verification_ref.artifact_version
        )
        != WORKFLOW_EVALUATION_VERIFICATION_VERSION
    ):
        raise AuthorityContractError(
            "authority.receipt.workflow_evaluation",
            "workflow evaluation verification evidence has another version",
        )
    for verification in receipt.principal_verifications:
        _require_reference_version(
            verification.signature_verification_ref,
            SIGNATURE_VERIFICATION_VERSION,
            "human signature verification",
        )
    for reference in receipt.signature_verification_refs:
        _require_reference_version(
            reference,
            SIGNATURE_VERIFICATION_VERSION,
            "grant signature verification",
        )
    human_counts = {
        AuthoritySource.ONE_SHOT_HUMAN: 1,
        AuthoritySource.DUAL_HUMAN: 2,
    }
    expected_humans = human_counts.get(receipt.authority_source)
    if expected_humans is not None:
        if (
            len(receipt.principal_verifications) != expected_humans
            or receipt.signature_verification_refs
        ):
            raise AuthorityContractError(
                "authority.receipt.signature",
                "human authority requires one distinct signature verification per principal",
            )
    elif receipt.principal_verifications:
        raise AuthorityContractError(
            "authority.receipt.principals",
            "non-human authority cannot carry human principal verifications",
        )
    if receipt.authority_source in {
        AuthoritySource.STANDING_GRANT,
        AuthoritySource.RELEASE_CAMPAIGN,
    } and not receipt.signature_verification_refs:
        raise AuthorityContractError(
            "authority.receipt.signature",
            "grant authority requires signature verification evidence",
        )
    if receipt.authority_source is AuthoritySource.STANDING_GRANT:
        if receipt.grant_sha256 is None:
            raise AuthorityContractError(
                "authority.receipt.grant",
                "standing authority receipt is not bound to a grant",
            )
    elif receipt.grant_sha256 is not None:
        raise AuthorityContractError(
            "authority.receipt.grant",
            "non-standing authority receipt cannot name a standing grant",
        )
    paired_refs = tuple(
        item.signature_verification_ref
        for item in receipt.principal_verifications
    )
    _validate_reference_set(
        receipt.signature_verification_refs,
        require_canonical_order=True,
    )
    authentication_refs = (
        receipt.requester_authentication_ref,
        *paired_refs,
        *receipt.signature_verification_refs,
    )
    content_distinct_proofs = (
        *authentication_refs,
        *(
            (receipt.workflow_evaluation_verification_ref,)
            if receipt.workflow_evaluation_verification_ref is not None
            else ()
        ),
    )
    proof_digests = tuple(
        str(value.sha256) for value in content_distinct_proofs
    )
    if len(proof_digests) != len(set(proof_digests)):
        raise AuthorityContractError(
            "authority.receipt.signature_content",
            "authentication, signature and workflow proofs must have distinct bytes",
        )
    _validate_reference_set(
        (
            receipt.ledger_entry,
            *(
                (receipt.workflow_evaluation_verification_ref,)
                if receipt.workflow_evaluation_verification_ref is not None
                else ()
            ),
            *authentication_refs,
        )
    )
    evaluated = parse_rfc3339_datetime(receipt.evaluated_at)
    checked = parse_rfc3339_datetime(receipt.revocation_checked_at)
    valid_until = parse_rfc3339_datetime(receipt.valid_until)
    if checked != evaluated or evaluated >= valid_until:
        raise AuthorityContractError("authority.receipt.window", "receipt time binding is invalid")
    digest = authority_verification_receipt_sha256(receipt)
    if str(digest) != str(receipt.receipt_sha256):
        raise AuthorityContractError("authority.receipt.digest", "receipt digest mismatch")
    if str(receipt.receipt_id) != f"authority-receipt-{str(digest)[:20]}":
        raise AuthorityContractError("authority.receipt.identity", "receipt id mismatch")
    return receipt


def _decision_identity(decision: AuthorityDecision) -> dict[str, object]:
    return {
        "artifact_version": decision.artifact_version,
        "action_request_sha256": str(decision.action_request_sha256),
        "gate_context_sha256": str(decision.gate_context_sha256),
        "risk_assessment_sha256": str(decision.risk_assessment_sha256),
        "effective_risk": decision.effective_risk.value,
        "required_authority": decision.required_authority.value,
        "status": decision.status.value,
        "source": decision.source.value,
        "reason_codes": list(decision.reason_codes),
        "matched_limit_sha256": (
            str(decision.matched_limit_sha256) if decision.matched_limit_sha256 else None
        ),
        "authority_basis_sha256": (
            str(decision.authority_basis_sha256)
            if decision.authority_basis_sha256 is not None
            else None
        ),
        "verification_receipt_id": (
            str(decision.verification_receipt_id) if decision.verification_receipt_id else None
        ),
        "verification_receipt_sha256": (
            str(decision.verification_receipt_sha256) if decision.verification_receipt_sha256 else None
        ),
        "evaluated_at": decision.evaluated_at,
        "valid_until": decision.valid_until,
        "predispatch_required": decision.predispatch_required,
        "authority_effect": decision.authority_effect,
    }


def authority_decision_to_mapping(decision: AuthorityDecision) -> dict[str, object]:
    validate_authority_decision(decision)
    return {
        **_decision_identity(decision),
        "decision_id": str(decision.decision_id),
        "decision_sha256": str(decision.decision_sha256),
    }


def validate_authority_decision(decision: AuthorityDecision) -> AuthorityDecision:
    if decision.artifact_version != AUTHORITY_DECISION_VERSION:
        raise AuthorityContractError("authority.decision.version", "unsupported decision version")
    if not decision.reason_codes or len(decision.reason_codes) != len(set(decision.reason_codes)):
        raise AuthorityContractError("authority.decision.reasons", "decision reasons must be non-empty and unique")
    authorized = decision.status is AuthorityDecisionStatus.AUTHORIZED
    if authorized != (decision.authority_effect == "execution_authority"):
        raise AuthorityContractError("authority.decision.effect", "decision authority effect is inconsistent")
    if authorized and decision.source is AuthoritySource.NONE:
        raise AuthorityContractError("authority.decision.source", "authorized decision has no source")
    if not authorized and decision.source is not AuthoritySource.NONE:
        raise AuthorityContractError("authority.decision.source", "non-authorized decision names a source")
    allowed_sources = {
        AuthorityRequirement.POLICY: {AuthoritySource.POLICY},
        AuthorityRequirement.STANDING_OR_ONE_HUMAN: {
            AuthoritySource.STANDING_GRANT,
            AuthoritySource.ONE_SHOT_HUMAN,
        },
        AuthorityRequirement.HUMAN_OR_CAMPAIGN: {
            AuthoritySource.ONE_SHOT_HUMAN,
        },
        AuthorityRequirement.TWO_INDEPENDENT_HUMANS: {
            AuthoritySource.DUAL_HUMAN,
        },
        AuthorityRequirement.PROHIBITED_UNTIL_IMPLEMENTED: set(),
    }
    if authorized and decision.source not in allowed_sources[decision.required_authority]:
        raise AuthorityContractError(
            "authority.decision.source",
            "authorized decision source does not satisfy the required authority",
        )
    if (decision.verification_receipt_id is None) != (decision.verification_receipt_sha256 is None):
        raise AuthorityContractError("authority.decision.receipt", "decision receipt binding is partial")
    has_receipt = decision.verification_receipt_id is not None
    if authorized and not has_receipt:
        raise AuthorityContractError(
            "authority.decision.receipt",
            "authorization requires an initial verification receipt",
        )
    if not authorized and has_receipt:
        raise AuthorityContractError(
            "authority.decision.receipt",
            "decision carries an unexpected verification receipt",
        )
    if authorized != (decision.matched_limit_sha256 is not None):
        raise AuthorityContractError(
            "authority.decision.limit",
            "decision limit binding is inconsistent with its status",
        )
    if authorized != (decision.authority_basis_sha256 is not None):
        raise AuthorityContractError(
            "authority.decision.basis",
            "decision authority basis is inconsistent with its status",
        )
    parse_rfc3339_datetime(decision.evaluated_at)
    if decision.valid_until is not None and parse_rfc3339_datetime(decision.evaluated_at) >= parse_rfc3339_datetime(decision.valid_until):
        raise AuthorityContractError("authority.decision.window", "decision validity window is empty")
    digest = canonical_sha256(_decision_identity(decision))
    if str(digest) != str(decision.decision_sha256):
        raise AuthorityContractError("authority.decision.digest", "decision digest mismatch")
    if str(decision.decision_id) != f"authority-decision-{str(digest)[:20]}":
        raise AuthorityContractError("authority.decision.identity", "decision id mismatch")
    return decision


def _build_decision(
    request: ActionAuthorityRequest,
    risk: ActionRiskAssessment,
    *,
    status: AuthorityDecisionStatus,
    source: AuthoritySource,
    reasons: Sequence[str],
    evaluated_at: datetime,
    required_authority: AuthorityRequirement,
    receipt: AuthorityVerificationReceipt | None,
) -> AuthorityDecision:
    valid_until = receipt.valid_until if receipt is not None else None
    matched = (
        canonical_sha256(
            {
                "scope": _scope_mapping(request.scope),
                "profiles": {
                    "assurance": request.profiles.assurance.value,
                    "autonomy": request.profiles.autonomy.value,
                },
            }
        )
        if status is AuthorityDecisionStatus.AUTHORIZED
        else None
    )
    provisional = AuthorityDecision(
        artifact_version=AUTHORITY_DECISION_VERSION,
        decision_id=OpaqueId("pending"),
        decision_sha256=HashDigest("0" * 64),
        action_request_sha256=request.request_sha256,
        gate_context_sha256=gate_context_sha256(request.gate_context),
        risk_assessment_sha256=risk.assessment_sha256,
        effective_risk=risk.effective_risk,
        required_authority=required_authority,
        status=status,
        source=source,
        reason_codes=tuple(dict.fromkeys(reasons)),
        matched_limit_sha256=matched,
        authority_basis_sha256=(
            _authority_basis_sha256(source, receipt)
            if status is AuthorityDecisionStatus.AUTHORIZED
            else None
        ),
        verification_receipt_id=receipt.receipt_id if receipt is not None else None,
        verification_receipt_sha256=receipt.receipt_sha256 if receipt is not None else None,
        evaluated_at=evaluated_at.isoformat(),
        valid_until=valid_until,
        predispatch_required=request.side_effect,
        authority_effect=("execution_authority" if status is AuthorityDecisionStatus.AUTHORIZED else "none"),
    )
    digest = canonical_sha256(_decision_identity(provisional))
    return validate_authority_decision(
        replace(
            provisional,
            decision_id=OpaqueId(f"authority-decision-{str(digest)[:20]}"),
            decision_sha256=digest,
        )
    )


def _validate_initial_receipt(
    receipt: AuthorityVerificationReceipt,
    request: ActionAuthorityRequest,
    risk: ActionRiskAssessment,
    policy: PolicyBundle,
    evaluated_at: datetime,
) -> None:
    validate_authority_verification_receipt(receipt)
    _validate_receipt_risk_window(receipt, risk, policy)
    expected_workflow_evaluation_sha256 = (
        request.workflow_evaluation.evaluation_sha256
        if request.workflow_evaluation is not None
        else None
    )
    if (
        receipt.purpose is not VerificationPurpose.INITIAL_DECISION
        or receipt.authority_decision_sha256 is not None
        or receipt.action_request_sha256 != request.request_sha256
        or receipt.gate_context_sha256 != gate_context_sha256(request.gate_context)
        or receipt.risk_assessment_sha256 != risk.assessment_sha256
        or receipt.workflow_evaluation_sha256
        != expected_workflow_evaluation_sha256
        or (
            request.workflow_evaluation is not None
            and receipt.workflow_evaluation_verification_ref is None
        )
        or (
            request.workflow_evaluation is None
            and receipt.workflow_evaluation_verification_ref is not None
        )
        or receipt.requester_principal_id != request.requester_principal_id
        or parse_rfc3339_datetime(receipt.evaluated_at) != evaluated_at
        or receipt.idempotency_key != request.idempotency_key
        or receipt.workspace_id != request.scope.workspace_id
        or receipt.workspace_observation_sha256 is not None
        or receipt.adapter_id is not None
        or receipt.service_identity is not None
        or receipt.reserved_cost_minor_units < request.scope.cost_minor_units
        or receipt.currency != request.scope.currency
        or receipt.reserved_candidates < request.scope.candidate_count
        or receipt.retry_index != request.scope.retry_index
    ):
        raise AuthorityContractError("authority.receipt.rebound", "initial receipt is bound to another request")


def _validate_receipt_risk_window(
    receipt: AuthorityVerificationReceipt,
    risk: ActionRiskAssessment,
    policy: PolicyBundle,
) -> None:
    if risk.effective_risk is not ActionRisk.R4:
        return
    evaluated = parse_rfc3339_datetime(receipt.evaluated_at)
    valid_until = parse_rfc3339_datetime(receipt.valid_until)
    if (
        valid_until - evaluated
    ).total_seconds() > policy.maximum_r4_validity_seconds:
        raise AuthorityContractError(
            "authority.r4.expiry_too_long",
            "R4 authority exceeds the target-owned short-expiry limit",
        )


def _source_allowed(
    requirement: AuthorityRequirement,
    risk: ActionRisk,
    source: AuthoritySource,
    principal_verifications: tuple[PrincipalSignatureVerification, ...],
    requester_principal_id: OpaqueId,
) -> bool:
    principals = tuple(item.principal_id for item in principal_verifications)
    if requester_principal_id in principals:
        return False
    if requirement is AuthorityRequirement.POLICY:
        return risk in {ActionRisk.R0, ActionRisk.R1} and source is AuthoritySource.POLICY
    if requirement is AuthorityRequirement.STANDING_OR_ONE_HUMAN:
        return source is AuthoritySource.STANDING_GRANT or (
            source is AuthoritySource.ONE_SHOT_HUMAN and len(principals) == 1
        )
    if requirement is AuthorityRequirement.HUMAN_OR_CAMPAIGN:
        # W05 has not yet supplied a trusted campaign-quality classifier or
        # immutable incident/content-risk facts.  Campaign authority therefore
        # remains fail-closed; one current independent human is the only W04
        # source for this requirement.
        return source is AuthoritySource.ONE_SHOT_HUMAN and len(principals) == 1
    if requirement is AuthorityRequirement.TWO_INDEPENDENT_HUMANS:
        return (
            risk is ActionRisk.R4
            and source is AuthoritySource.DUAL_HUMAN
            and len(principals) == 2
            and len(set(principals)) == 2
        )
    return False


_AUTHORITY_REQUIREMENT_RANK = {
    AuthorityRequirement.POLICY: 0,
    AuthorityRequirement.STANDING_OR_ONE_HUMAN: 1,
    AuthorityRequirement.HUMAN_OR_CAMPAIGN: 2,
    AuthorityRequirement.TWO_INDEPENDENT_HUMANS: 3,
    AuthorityRequirement.PROHIBITED_UNTIL_IMPLEMENTED: 4,
}


def _effective_authority_requirement(
    request: ActionAuthorityRequest,
    risk: ActionRiskAssessment,
) -> AuthorityRequirement:
    risk_floor = {
        ActionRisk.R0: AuthorityRequirement.POLICY,
        ActionRisk.R1: AuthorityRequirement.POLICY,
        ActionRisk.R2: AuthorityRequirement.STANDING_OR_ONE_HUMAN,
        ActionRisk.R3: AuthorityRequirement.HUMAN_OR_CAMPAIGN,
        ActionRisk.R4: AuthorityRequirement.TWO_INDEPENDENT_HUMANS,
    }[risk.effective_risk]
    return max(
        (request.authority_requirement, risk_floor),
        key=_AUTHORITY_REQUIREMENT_RANK.__getitem__,
    )


def evaluate_authority(
    request: ActionAuthorityRequest,
    policy: PolicyBundle,
    *,
    ledger: TrustedAuthorizationLedger | None,
    presented_grant: UnverifiedStandingAuthorization | None = None,
    authority_references: Sequence[ArtifactReference] = (),
    evaluated_at: datetime,
) -> tuple[ActionRiskAssessment, AuthorityDecision]:
    """Evaluate authority without trusting modes, AI consensus, or raw documents."""

    _require_aware(evaluated_at, "evaluated_at")
    validate_action_authority_request(request)
    require_target_policy_bundle(policy)
    if str(request.gate_context.policy_bundle_sha256) != str(policy.bundle_sha256):
        raise AuthorityContractError("authority.context.policy", "request context uses another policy bundle")
    risk = classify_action_risk(request, policy)
    if not risk.supported:
        return risk, _build_decision(
            request, risk, status=AuthorityDecisionStatus.DENIED, source=AuthoritySource.NONE,
            reasons=risk.reason_codes, evaluated_at=evaluated_at,
            required_authority=AuthorityRequirement.PROHIBITED_UNTIL_IMPLEMENTED,
            receipt=None,
        )
    required = _effective_authority_requirement(request, risk)
    references = _validate_reference_set(tuple(authority_references))
    if presented_grant is not None:
        if risk.effective_risk is ActionRisk.R4:
            return risk, _build_decision(
                request, risk, status=AuthorityDecisionStatus.DENIED, source=AuthoritySource.NONE,
                reasons=("authority.r4.standing_forbidden",), evaluated_at=evaluated_at,
                required_authority=required, receipt=None,
            )
        blockers = _standing_scope_blockers(presented_grant, request, risk, evaluated_at)
        if blockers:
            return risk, _build_decision(
                request, risk, status=AuthorityDecisionStatus.DENIED, source=AuthoritySource.NONE,
                reasons=blockers, evaluated_at=evaluated_at, required_authority=required,
                receipt=None,
            )
    missing_authority_evidence = (
        required is not AuthorityRequirement.POLICY
        and presented_grant is None
        and not references
    )
    if ledger is None or missing_authority_evidence:
        missing_reasons = (
            (*risk.reason_codes, "authority.evidence.missing")
            if risk.effective_risk is not request.action_risk
            else ("authority.evidence.missing",)
        )
        return risk, _build_decision(
            request,
            risk,
            status=(
                AuthorityDecisionStatus.DENIED
                if required is AuthorityRequirement.POLICY
                else AuthorityDecisionStatus.HUMAN_APPROVAL_REQUIRED
            ),
            source=AuthoritySource.NONE, reasons=missing_reasons,
            evaluated_at=evaluated_at, required_authority=required, receipt=None,
        )
    try:
        receipt = ledger.verify_current(
            request, risk, presented_grant, references,
            current_context=request.gate_context, evaluated_at=evaluated_at,
        )
    except Exception as error:
        raise AuthorityContractError("authority.ledger.unavailable", "trusted ledger verification failed") from error
    if receipt is None:
        return risk, _build_decision(
            request, risk, status=AuthorityDecisionStatus.DENIED, source=AuthoritySource.NONE,
            reasons=("authority.ledger.denied",), evaluated_at=evaluated_at,
            required_authority=required, receipt=None,
        )
    _validate_initial_receipt(receipt, request, risk, policy, evaluated_at)
    verified_principals = tuple(
        item.principal_id for item in receipt.principal_verifications
    )
    if request.requester_principal_id in verified_principals:
        return risk, _build_decision(
            request,
            risk,
            status=AuthorityDecisionStatus.DENIED,
            source=AuthoritySource.NONE,
            reasons=("authority.self_approval_forbidden",),
            evaluated_at=evaluated_at,
            required_authority=required,
            receipt=None,
        )
    if not _source_allowed(
        required,
        risk.effective_risk,
        receipt.authority_source,
        receipt.principal_verifications,
        request.requester_principal_id,
    ):
        return risk, _build_decision(
            request, risk, status=AuthorityDecisionStatus.DENIED, source=AuthoritySource.NONE,
            reasons=("authority.source.insufficient",), evaluated_at=evaluated_at,
            required_authority=required, receipt=None,
        )
    if receipt.authority_source is AuthoritySource.STANDING_GRANT:
        if presented_grant is None or receipt.grant_sha256 != presented_grant.authorization_sha256:
            raise AuthorityContractError("authority.receipt.grant", "ledger receipt is not bound to the presented grant")
    return risk, _build_decision(
        request, risk, status=AuthorityDecisionStatus.AUTHORIZED,
        source=receipt.authority_source, reasons=("authority.ledger.current",),
        evaluated_at=evaluated_at, required_authority=required, receipt=receipt,
    )


def _approval_identity(request: ApprovalRequest) -> dict[str, object]:
    return {
        "artifact_version": request.artifact_version,
        "action_request_sha256": str(request.action_request_sha256),
        "gate_context_sha256": str(request.gate_context_sha256),
        "risk_assessment_sha256": str(request.risk_assessment_sha256),
        "required_authority": request.required_authority.value,
        "required_independent_humans": request.required_independent_humans,
        "reason_codes": list(request.reason_codes),
        "material_diff_sha256": str(request.material_diff_sha256),
        "safe_default": request.safe_default.value,
        "creates_authority": request.creates_authority,
        "authority_effect": request.authority_effect,
    }


def build_approval_request(
    request: ActionAuthorityRequest,
    risk: ActionRiskAssessment,
    decision: AuthorityDecision,
) -> ApprovalRequest:
    validate_action_authority_request(request)
    validate_risk_assessment(risk)
    validate_authority_decision(decision)
    policy = target_policy_bundle()
    expected_risk = classify_action_risk(request, policy)
    if (
        request.gate_context.policy_bundle_sha256 != policy.bundle_sha256
        or
        risk != expected_risk
        or risk.action_request_sha256 != request.request_sha256
        or decision.action_request_sha256 != request.request_sha256
        or decision.gate_context_sha256 != gate_context_sha256(request.gate_context)
        or decision.risk_assessment_sha256 != risk.assessment_sha256
        or decision.effective_risk is not risk.effective_risk
    ):
        raise AuthorityContractError(
            "authority.approval_request.rebound",
            "approval request inputs are not bound to one current action",
        )
    if decision.status is AuthorityDecisionStatus.AUTHORIZED:
        raise AuthorityContractError("authority.approval_request.authorized", "authorized action needs no approval request")
    count = 2 if risk.effective_risk is ActionRisk.R4 else 1
    provisional = ApprovalRequest(
        artifact_version=APPROVAL_REQUEST_VERSION,
        approval_request_id=OpaqueId("pending"),
        approval_request_sha256=HashDigest("0" * 64),
        action_request_sha256=request.request_sha256,
        gate_context_sha256=gate_context_sha256(request.gate_context),
        risk_assessment_sha256=risk.assessment_sha256,
        required_authority=decision.required_authority,
        required_independent_humans=count,
        reason_codes=decision.reason_codes,
        material_diff_sha256=canonical_sha256(
            {"plan_sha256": str(request.executable_plan_sha256), "scope": _scope_mapping(request.scope)}
        ),
        safe_default=AuthorityDecisionStatus.DENIED,
        creates_authority=False,
        authority_effect="none",
    )
    digest = canonical_sha256(_approval_identity(provisional))
    return validate_approval_request(
        replace(
            provisional,
            approval_request_id=OpaqueId(f"approval-request-{str(digest)[:20]}"),
            approval_request_sha256=digest,
        )
    )


def approval_request_to_mapping(request: ApprovalRequest) -> dict[str, object]:
    validate_approval_request(request)
    return {
        **_approval_identity(request),
        "approval_request_id": str(request.approval_request_id),
        "approval_request_sha256": str(request.approval_request_sha256),
    }


def validate_approval_request(request: ApprovalRequest) -> ApprovalRequest:
    if (
        request.artifact_version != APPROVAL_REQUEST_VERSION
        or request.creates_authority is not False
        or request.authority_effect != "none"
        or request.safe_default is not AuthorityDecisionStatus.DENIED
        or request.required_independent_humans not in {1, 2}
    ):
        raise AuthorityContractError("authority.approval_request.contract", "approval request is not non-authorizing")
    digest = canonical_sha256(_approval_identity(request))
    if str(digest) != str(request.approval_request_sha256):
        raise AuthorityContractError("authority.approval_request.digest", "approval request digest mismatch")
    if str(request.approval_request_id) != f"approval-request-{str(digest)[:20]}":
        raise AuthorityContractError("authority.approval_request.identity", "approval request id mismatch")
    return request


def revalidate_authority_for_side_effect(
    decision: AuthorityDecision,
    request: ActionAuthorityRequest,
    *,
    ledger: TrustedAuthorizationLedger | None,
    current_context: GateContext,
    workspace_observation_sha256: str,
    adapter_id: str,
    service_identity: str,
    evaluated_at: datetime,
    purpose: VerificationPurpose,
) -> VerifiedAuthorityDecision:
    """Obtain a fresh, purpose-bound receipt immediately before a side effect."""

    _require_aware(evaluated_at, "evaluated_at")
    validate_authority_decision(decision)
    validate_action_authority_request(request)
    policy = target_policy_bundle()
    if request.gate_context.policy_bundle_sha256 != policy.bundle_sha256:
        raise AuthorityContractError(
            "authority.predispatch.policy",
            "request is not bound to the current target authority policy",
        )
    risk = classify_action_risk(request, policy)
    if not risk.supported:
        raise AuthorityContractError(
            "authority.predispatch.risk_unsupported",
            "current risk assessment is unsupported for side-effect authorization",
        )
    required = _effective_authority_requirement(request, risk)
    expected_limit = canonical_sha256(
        {
            "scope": _scope_mapping(request.scope),
            "profiles": {
                "assurance": request.profiles.assurance.value,
                "autonomy": request.profiles.autonomy.value,
            },
        }
    )
    if (
        decision.risk_assessment_sha256 != risk.assessment_sha256
        or decision.effective_risk is not risk.effective_risk
        or decision.required_authority is not required
        or decision.matched_limit_sha256 != expected_limit
    ):
        raise AuthorityContractError(
            "authority.predispatch.decision_rebound",
            "decision risk, authority, or limits differ from the current request",
        )
    if purpose not in {VerificationPurpose.DISPATCH, VerificationPurpose.RECONCILE, VerificationPurpose.MUTATION}:
        raise AuthorityContractError("authority.predispatch.purpose", "side-effect purpose is invalid")
    if decision.status is not AuthorityDecisionStatus.AUTHORIZED or not decision.predispatch_required:
        raise AuthorityContractError("authority.predispatch.decision", "decision cannot authorize a side effect")
    if decision.action_request_sha256 != request.request_sha256:
        raise AuthorityContractError("authority.predispatch.request", "decision is bound to another request")
    if current_context != request.gate_context or decision.gate_context_sha256 != gate_context_sha256(current_context):
        raise AuthorityContractError("authority.predispatch.context", "decision is stale for current context")
    if decision.valid_until is not None and evaluated_at >= parse_rfc3339_datetime(decision.valid_until):
        raise AuthorityContractError("authority.predispatch.expired", "authority decision has expired")
    if evaluated_at < parse_rfc3339_datetime(decision.evaluated_at):
        raise AuthorityContractError(
            "authority.predispatch.not_yet_valid",
            "authority decision is not yet valid",
        )
    if ledger is None:
        raise AuthorityContractError("authority.predispatch.ledger", "trusted ledger is required")
    if (
        len(workspace_observation_sha256) != 64
        or workspace_observation_sha256.lower() != workspace_observation_sha256
        or any(value not in "0123456789abcdef" for value in workspace_observation_sha256)
    ):
        raise AuthorityContractError(
            "authority.predispatch.workspace",
            "workspace observation digest is invalid",
        )
    workspace_digest = HashDigest(workspace_observation_sha256)
    try:
        receipt = ledger.revalidate_and_reserve_current(
            decision, request, current_context=current_context,
            workspace_observation_sha256=workspace_digest,
            adapter_id=OpaqueId(adapter_id), service_identity=OpaqueId(service_identity),
            evaluated_at=evaluated_at, purpose=purpose,
        )
    except Exception as error:
        raise AuthorityContractError("authority.predispatch.unavailable", "trusted predispatch verification failed") from error
    if receipt is None:
        raise AuthorityContractError("authority.predispatch.denied", "trusted predispatch verification denied")
    validate_authority_verification_receipt(receipt)
    _validate_receipt_risk_window(receipt, risk, policy)
    if (
        receipt.purpose is not purpose
        or receipt.authority_decision_sha256 != decision.decision_sha256
        or receipt.authority_source is not decision.source
        or _authority_basis_sha256(receipt.authority_source, receipt)
        != decision.authority_basis_sha256
        or receipt.action_request_sha256 != request.request_sha256
        or receipt.risk_assessment_sha256 != risk.assessment_sha256
        or receipt.workflow_evaluation_sha256
        != (
            request.workflow_evaluation.evaluation_sha256
            if request.workflow_evaluation is not None
            else None
        )
        or (
            request.workflow_evaluation is not None
            and receipt.workflow_evaluation_verification_ref is None
        )
        or (
            request.workflow_evaluation is None
            and receipt.workflow_evaluation_verification_ref is not None
        )
        or receipt.requester_principal_id != request.requester_principal_id
        or receipt.gate_context_sha256 != gate_context_sha256(current_context)
        or receipt.idempotency_key != request.idempotency_key
        or receipt.workspace_id != request.scope.workspace_id
        or receipt.workspace_observation_sha256 != workspace_digest
        or str(receipt.adapter_id) != adapter_id
        or str(receipt.service_identity) != service_identity
        or parse_rfc3339_datetime(receipt.evaluated_at) != evaluated_at
        or receipt.reserved_cost_minor_units < request.scope.cost_minor_units
        or receipt.currency != request.scope.currency
        or receipt.reserved_candidates < request.scope.candidate_count
        or receipt.retry_index != request.scope.retry_index
    ):
        raise AuthorityContractError("authority.predispatch.rebound", "predispatch receipt is bound to another action")
    if not _source_allowed(
        required,
        risk.effective_risk,
        receipt.authority_source,
        receipt.principal_verifications,
        request.requester_principal_id,
    ):
        raise AuthorityContractError(
            "authority.predispatch.source",
            "predispatch authority source or principals no longer satisfy policy",
        )
    return VerifiedAuthorityDecision(
        decision=decision,
        receipt=receipt,
        request_sha256=request.request_sha256,
        purpose=purpose,
    )
