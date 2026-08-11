"""Workflow policy catalog and ADR-004 bridge (ADR-006)."""

from video_factory._mode_contracts import WorkflowMode

from .bridge import (
    assert_executor_automated_permitted,
    execution_mode_ceiling,
    executor_automated_permitted,
    human_evidence_required,
    human_evidence_required_for_mode,
)
from .catalog import (
    CONTROLLED_POLICY,
    RAPID_POLICY,
    STANDARD_POLICY,
    WORKFLOW_POLICIES,
)
from .models import (
    STAGE_BRIEF,
    STAGE_FINAL_VIDEO,
    STAGE_GENERATION_PLAN,
    STAGE_STORYBOARD,
    ApprovalKind,
    ExecutorAutomatedEvidence,
    ReviewMode,
    WorkflowPolicy,
    WorkflowPolicyError,
)
from .resolve import parse_workflow_mode, resolve_workflow_policy

__all__ = [
    "ApprovalKind",
    "CONTROLLED_POLICY",
    "ExecutorAutomatedEvidence",
    "RAPID_POLICY",
    "ReviewMode",
    "STAGE_BRIEF",
    "STAGE_FINAL_VIDEO",
    "STAGE_GENERATION_PLAN",
    "STAGE_STORYBOARD",
    "STANDARD_POLICY",
    "WORKFLOW_POLICIES",
    "WorkflowMode",
    "WorkflowPolicy",
    "WorkflowPolicyError",
    "assert_executor_automated_permitted",
    "execution_mode_ceiling",
    "executor_automated_permitted",
    "human_evidence_required",
    "human_evidence_required_for_mode",
    "parse_workflow_mode",
    "resolve_workflow_policy",
]
