"""Workflow policy data shapes (ADR-006).

Workflow mode (Rapid / Standard / Controlled) is a separate axis from adapter
execution mode (preview_only / human_only / automated). These types describe
approval, review, and audit strictness only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from video_factory.engine.contracts import ExecutionMode, WorkflowMode


class WorkflowPolicyError(ValueError):
    """Raised when a workflow mode is missing, unknown, or violates a policy gate."""


class ApprovalKind(StrEnum):
    """Kinds of human approval a workflow mode may require (ADR-006)."""

    GENERATION_APPROVAL = "generation_approval"
    PUBLISH_APPROVAL = "publish_approval"
    DESTRUCTIVE_ACTION_APPROVAL = "destructive_action_approval"
    POLICY_EXCEPTION_APPROVAL = "policy_exception_approval"


class ReviewMode(StrEnum):
    """How review is performed at a named stage (ADR-006 review_policy)."""

    NONE = "none"
    SELF_CHECK = "self_check"
    AUTOMATED_LINT = "automated_lint"
    PEER_AI = "peer_ai"
    HUMAN = "human"
    PEER_AI_AND_HUMAN = "peer_ai_and_human"
    TWO_INDEPENDENT_REVIEWERS = "two_independent_reviewers"


# Stage keys used in review_policy_by_stage. Callers may use additional stage
# names; the catalog below documents the ADR-006 baseline set.
STAGE_BRIEF = "brief"
STAGE_STORYBOARD = "storyboard"
STAGE_GENERATION_PLAN = "generation_plan"
STAGE_FINAL_VIDEO = "final_video"


@dataclass(frozen=True, slots=True)
class WorkflowPolicy:
    """Capability combination for one workflow mode (ADR-006 mode definition).

    This is the definition of a mode, not a production default. Callers must
    choose a mode explicitly; the core never invents one (OD-004 open).
    """

    mode: WorkflowMode
    required_approval_kinds: frozenset[ApprovalKind]
    review_policy_by_stage: Mapping[str, ReviewMode]
    hash_binding_required: bool
    author_reviewer_separation_required: bool
    immutable_audit_log_required: bool
    allows_capability_downgrade_mid_run: bool
    # Ceiling for adapter execution mode under this workflow mode (ADR-006 table).
    # Distinct from the workflow mode itself.
    maximum_execution_mode: ExecutionMode


@dataclass(frozen=True, slots=True)
class ExecutorAutomatedEvidence:
    """Preconditions for executor automated dispatch under Controlled (ADR-004).

    All flags must be True. The core does not invent them; the orchestrator
    supplies facts from reservation, kill-switch, and hash-bound approval records.
    """

    manual_window_approved: bool
    task_hash_bound: bool
    reservation_recorded: bool
    kill_switch_clear: bool

    @property
    def is_complete(self) -> bool:
        return (
            self.manual_window_approved
            and self.task_hash_bound
            and self.reservation_recorded
            and self.kill_switch_clear
        )
