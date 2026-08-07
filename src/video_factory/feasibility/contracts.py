"""Channel-neutral generation-feasibility plan and judgment contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from video_factory.domain import ArtifactReference, OpaqueId, RoleId
from video_factory.providers import CapabilityConstraintProfile


class FeasibilityCheckKind(StrEnum):
    CAPABILITY_BINDING = "capability_binding"
    MINIMUM_DURATION = "minimum_duration"
    FIRST_FRAME_ASPECT = "first_frame_aspect"
    FIRST_FRAME_BEFORE_STATE = "first_frame_before_state"
    CONTINUITY_ANCHOR = "continuity_anchor"
    FIRST_FRAME_STATE_CARRYOVER = "first_frame_state_carryover"
    UNSUPPORTED_RENDER_DEPENDENCY = "unsupported_render_dependency"


class FeasibilityStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True, slots=True)
class FeasibilityCheck:
    check_id: OpaqueId
    kind: FeasibilityCheckKind
    shot_id: OpaqueId
    status: FeasibilityStatus
    message: str


@dataclass(frozen=True, slots=True)
class GenerationFeasibilityJudgment:
    packet: ArtifactReference
    storyboard: ArtifactReference
    capability_profile: CapabilityConstraintProfile
    creator_role: RoleId
    reviewer_role: RoleId
    checks: tuple[FeasibilityCheck, ...]
    overall: FeasibilityStatus

    @property
    def passed(self) -> bool:
        return self.overall is FeasibilityStatus.PASS
