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
class ApprovalRequirement:
    requirement_id: OpaqueId
    capability_id: OpaqueId
    bound_artifacts: tuple[ArtifactReference, ...]
    effective_config_sha256: HashDigest


@dataclass(frozen=True, slots=True)
class ApprovalEvidence:
    evidence_id: OpaqueId
    requirement: ApprovalRequirement
    state: ApprovalState
    approver_role: RoleId
    created_at: str
    record_sha256: HashDigest


class HumanGate(Protocol):
    def require(self, requirement: ApprovalRequirement) -> ApprovalEvidence: ...

