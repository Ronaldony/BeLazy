"""Immutable, hash-bound human gate shapes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from video_factory.domain import ArtifactReference, HashDigest, OpaqueId, RoleId


class ApprovalState(StrEnum):
    GRANTED = "granted"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class GateContext:
    """Exact material context against which approval may authorize an action."""

    workflow_definition_sha256: HashDigest
    policy_bundle_sha256: HashDigest
    rules_bundle_sha256: HashDigest
    effective_config_sha256: HashDigest
    current_manifest_sha256: HashDigest
    evidence_graph_sha256: HashDigest
    executable_plan_sha256: HashDigest


@dataclass(frozen=True, slots=True)
class ApprovalRequirement:
    requirement_id: OpaqueId
    capability_id: OpaqueId
    bound_artifacts: tuple[ArtifactReference, ...]
    effective_config_sha256: HashDigest
    gate_context: GateContext | None = None


@dataclass(frozen=True, slots=True)
class ApprovalEvidence:
    evidence_id: OpaqueId
    requirement: ApprovalRequirement
    state: ApprovalState
    approver_role: RoleId
    created_at: str
    record_sha256: HashDigest
    expires_at: str | None = None


class HumanGate(Protocol):
    def require(self, requirement: ApprovalRequirement) -> ApprovalEvidence: ...
