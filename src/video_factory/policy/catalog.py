"""Concrete WorkflowPolicy values for each ADR-006 mode.

Values here are the definition of each mode, not an ambient package default.
Selecting a mode is the caller's responsibility (OD-004 remains open).
"""

from __future__ import annotations

from types import MappingProxyType

from video_factory.engine.contracts import ExecutionMode, WorkflowMode

from .models import (
    STAGE_BRIEF,
    STAGE_FINAL_VIDEO,
    STAGE_GENERATION_PLAN,
    STAGE_STORYBOARD,
    ApprovalKind,
    ReviewMode,
    WorkflowPolicy,
)

_RAPID_REVIEW = MappingProxyType(
    {
        STAGE_BRIEF: ReviewMode.SELF_CHECK,
        STAGE_STORYBOARD: ReviewMode.AUTOMATED_LINT,
        STAGE_GENERATION_PLAN: ReviewMode.NONE,
        STAGE_FINAL_VIDEO: ReviewMode.NONE,
    }
)

_STANDARD_REVIEW = MappingProxyType(
    {
        STAGE_BRIEF: ReviewMode.SELF_CHECK,
        # Optional single peer review when a human requests it; AI PASS never
        # replaces Generation/Publish Release (ADR-006 Standard checkpoints).
        STAGE_STORYBOARD: ReviewMode.PEER_AI,
        STAGE_GENERATION_PLAN: ReviewMode.HUMAN,
        STAGE_FINAL_VIDEO: ReviewMode.HUMAN,
    }
)

_CONTROLLED_REVIEW = MappingProxyType(
    {
        STAGE_BRIEF: ReviewMode.HUMAN,
        STAGE_STORYBOARD: ReviewMode.PEER_AI_AND_HUMAN,
        STAGE_GENERATION_PLAN: ReviewMode.PEER_AI_AND_HUMAN,
        STAGE_FINAL_VIDEO: ReviewMode.TWO_INDEPENDENT_REVIEWERS,
    }
)

# Rapid: draft / local preflight only. No paid generation or publish path.
RAPID_POLICY = WorkflowPolicy(
    mode=WorkflowMode.RAPID,
    required_approval_kinds=frozenset(),
    review_policy_by_stage=_RAPID_REVIEW,
    hash_binding_required=False,
    author_reviewer_separation_required=False,
    immutable_audit_log_required=False,
    allows_capability_downgrade_mid_run=False,
    maximum_execution_mode=ExecutionMode.PREVIEW_ONLY,
)

# Standard: exactly two human checkpoints — generation and publish release.
STANDARD_POLICY = WorkflowPolicy(
    mode=WorkflowMode.STANDARD,
    required_approval_kinds=frozenset(
        {
            ApprovalKind.GENERATION_APPROVAL,
            ApprovalKind.PUBLISH_APPROVAL,
        }
    ),
    review_policy_by_stage=_STANDARD_REVIEW,
    # Composite checkpoints bind artifact path/hash; continuous strict ledger
    # and author/reviewer matrix remain Controlled-only invariants.
    hash_binding_required=True,
    author_reviewer_separation_required=False,
    immutable_audit_log_required=False,
    allows_capability_downgrade_mid_run=False,
    maximum_execution_mode=ExecutionMode.HUMAN_ONLY,
)

# Controlled: Standard approvals plus strict audit capabilities. Destructive
# and policy-exception gates are part of the Controlled surface so legacy
# fine-grained approvals and recovery paths are not deleted (ADR-006).
CONTROLLED_POLICY = WorkflowPolicy(
    mode=WorkflowMode.CONTROLLED,
    required_approval_kinds=frozenset(
        {
            ApprovalKind.GENERATION_APPROVAL,
            ApprovalKind.PUBLISH_APPROVAL,
            ApprovalKind.DESTRUCTIVE_ACTION_APPROVAL,
            ApprovalKind.POLICY_EXCEPTION_APPROVAL,
        }
    ),
    review_policy_by_stage=_CONTROLLED_REVIEW,
    hash_binding_required=True,
    author_reviewer_separation_required=True,
    immutable_audit_log_required=True,
    allows_capability_downgrade_mid_run=False,
    maximum_execution_mode=ExecutionMode.AUTOMATED,
)

WORKFLOW_POLICIES: MappingProxyType[WorkflowMode, WorkflowPolicy] = MappingProxyType(
    {
        WorkflowMode.RAPID: RAPID_POLICY,
        WorkflowMode.STANDARD: STANDARD_POLICY,
        WorkflowMode.CONTROLLED: CONTROLLED_POLICY,
    }
)
