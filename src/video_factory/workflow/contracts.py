"""Versioned, side-effect-free declarative workflow contracts.

The shapes in this module describe observations and plans.  They never grant
execution authority: an executable production plan still needs a separately
verified authority decision for its exact :class:`GateContext`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from video_factory.approvals import GateContext
from video_factory.domain import HashDigest, OpaqueId


class WorkflowContractError(ValueError):
    """A workflow artifact or graph violates a closed contract."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class GateStatus(StrEnum):
    PASS = "pass"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class AssuranceProfile(StrEnum):
    DRAFT = "draft"
    PRODUCTION = "production"
    HIGH_ASSURANCE = "high_assurance"


class AutonomyProfile(StrEnum):
    OBSERVE_ONLY = "observe_only"
    ASSISTED = "assisted"
    GUARDED_AUTONOMOUS = "guarded_autonomous"
    BOUNDED_AUTONOMOUS = "bounded_autonomous"


class ActionRisk(StrEnum):
    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    R4 = "R4"


class AuthorityRequirement(StrEnum):
    POLICY = "policy"
    STANDING_OR_ONE_HUMAN = "standing_or_one_human"
    HUMAN_OR_CAMPAIGN = "human_or_campaign"
    TWO_INDEPENDENT_HUMANS = "two_independent_humans"
    PROHIBITED_UNTIL_IMPLEMENTED = "prohibited_until_implemented"


@dataclass(frozen=True, slots=True)
class MaterialContextSeed:
    """The six material digests shared before an action-specific plan exists."""

    workflow_definition_sha256: HashDigest
    policy_bundle_sha256: HashDigest
    rules_bundle_sha256: HashDigest
    effective_config_sha256: HashDigest
    current_manifest_sha256: HashDigest
    evidence_graph_sha256: HashDigest


@dataclass(frozen=True, slots=True)
class ClaimDefinition:
    claim_id: OpaqueId
    dependency_claim_ids: tuple[OpaqueId, ...]
    gate_id: OpaqueId


@dataclass(frozen=True, slots=True)
class GateDefinition:
    gate_id: OpaqueId
    owner: str
    stable_reason_prefix: str
    consumed_context_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ActionDefinition:
    action_id: OpaqueId
    satisfies_claim_id: OpaqueId
    required_claim_ids: tuple[OpaqueId, ...]
    actor_role: str
    capability_id: OpaqueId
    risk: ActionRisk
    authority_requirement: AuthorityRequirement
    priority: int
    side_effect: bool
    trigger_reason_codes: tuple[str, ...]
    prohibited_actions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    artifact_version: str
    workflow_id: OpaqueId
    workflow_version: str
    definition_sha256: HashDigest
    claims: tuple[ClaimDefinition, ...]
    gates: tuple[GateDefinition, ...]
    actions: tuple[ActionDefinition, ...]


@dataclass(frozen=True, slots=True)
class GateResult:
    artifact_version: str
    gate_id: OpaqueId
    consumed_context_sha256: HashDigest
    status: GateStatus
    reason_codes: tuple[str, ...]
    messages: tuple[str, ...]
    evidence_sha256s: tuple[HashDigest, ...]
    result_sha256: HashDigest


@dataclass(frozen=True, slots=True)
class ActionBlocker:
    claim_id: OpaqueId
    gate_id: OpaqueId
    reason_code: str
    message: str


@dataclass(frozen=True, slots=True)
class ActionFrontierItem:
    action_id: OpaqueId
    plan_sha256: HashDigest
    actor_role: str
    capability_id: OpaqueId
    risk: ActionRisk
    authority_requirement: AuthorityRequirement
    consumed_claim_ids: tuple[OpaqueId, ...]
    consumed_evidence_sha256s: tuple[HashDigest, ...]
    blockers: tuple[ActionBlocker, ...]
    prohibited_actions: tuple[str, ...]
    authority_effect: str = "none"


@dataclass(frozen=True, slots=True)
class WorkflowEvaluation:
    artifact_version: str
    evaluation_id: OpaqueId
    evaluation_sha256: HashDigest
    workflow_id: OpaqueId
    workflow_definition_sha256: HashDigest
    material_context: MaterialContextSeed
    material_context_sha256: HashDigest
    gate_results: tuple[GateResult, ...]
    gate_input_sha256s: tuple[tuple[OpaqueId, HashDigest], ...]
    satisfied_claim_ids: tuple[OpaqueId, ...]
    blockers: tuple[ActionBlocker, ...]
    action_frontier: tuple[ActionFrontierItem, ...]
    recommended_action_id: OpaqueId | None
    invalidated_claim_ids: tuple[OpaqueId, ...]
    reused_claim_ids: tuple[OpaqueId, ...]
    authority_effect: str = "none"


@dataclass(frozen=True, slots=True)
class ExecutableProductionPlan:
    artifact_version: str
    plan_id: OpaqueId
    plan_sha256: HashDigest
    workflow_evaluation_sha256: HashDigest
    workflow_definition_sha256: HashDigest
    action_id: OpaqueId
    capability_id: OpaqueId
    actor_role: str
    risk: ActionRisk
    authority_requirement: AuthorityRequirement
    side_effect: bool
    gate_context: GateContext
    consumed_claim_ids: tuple[OpaqueId, ...]
    consumed_evidence_sha256s: tuple[HashDigest, ...]
    prohibited_actions: tuple[str, ...]
    authority_effect: str = "none"


@dataclass(frozen=True, slots=True)
class LegacyNextStepProjection:
    action_type: str
    actor_role: str | None
    approval_required: bool
    blockers: tuple[str, ...]
    consumed_evidence: tuple[str, ...]
    prohibited_actions: tuple[str, ...]
    authority_effect: str = "none"


@dataclass(frozen=True, slots=True)
class ParityDifference:
    dimension: str
    legacy_value_sha256: HashDigest
    declarative_value_sha256: HashDigest
    explanation_code: str | None


@dataclass(frozen=True, slots=True)
class WorkflowParityReport:
    artifact_version: str
    report_id: OpaqueId
    report_sha256: HashDigest
    normalization_version: str
    normalization_sha256: HashDigest
    legacy_projection_sha256: HashDigest
    workflow_evaluation_sha256: HashDigest
    differences: tuple[ParityDifference, ...]
    unexplained_dimensions: tuple[str, ...]
    parity_pass: bool
    cutover_applied: bool = False
    authority_effect: str = "none"
