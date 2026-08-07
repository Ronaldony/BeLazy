"""Synthetic tests for ADR-006 workflow policy and ADR-004 bridge."""

from __future__ import annotations

import pytest

from video_factory.engine import ExecutionMode, WorkflowMode
from video_factory.policy import (
    ApprovalKind,
    CONTROLLED_POLICY,
    ExecutorAutomatedEvidence,
    RAPID_POLICY,
    STANDARD_POLICY,
    WorkflowPolicyError,
    assert_executor_automated_permitted,
    executor_automated_permitted,
    human_evidence_required,
    resolve_workflow_policy,
)
from video_factory.providers import ModeEnforcementError


def _complete_evidence() -> ExecutorAutomatedEvidence:
    return ExecutorAutomatedEvidence(
        manual_window_approved=True,
        task_hash_bound=True,
        reservation_recorded=True,
        kill_switch_clear=True,
    )


def test_resolve_workflow_policy_rejects_none_without_silent_default() -> None:
    with pytest.raises(WorkflowPolicyError, match="does not choose a production default"):
        resolve_workflow_policy(None)


def test_resolve_workflow_policy_rejects_unknown_mode() -> None:
    with pytest.raises(WorkflowPolicyError, match="unknown workflow mode"):
        resolve_workflow_policy("not-a-mode")


def test_rapid_has_no_approval_kinds_and_blocks_paid_paths() -> None:
    policy = resolve_workflow_policy("rapid")
    assert policy is RAPID_POLICY
    assert policy.required_approval_kinds == frozenset()
    assert ApprovalKind.GENERATION_APPROVAL not in policy.required_approval_kinds
    assert ApprovalKind.PUBLISH_APPROVAL not in policy.required_approval_kinds
    assert policy.hash_binding_required is False
    assert human_evidence_required(policy) is False
    assert policy.maximum_execution_mode is ExecutionMode.PREVIEW_ONLY


def test_standard_requires_exactly_generation_and_publish_approvals() -> None:
    policy = resolve_workflow_policy(WorkflowMode.STANDARD)
    assert policy is STANDARD_POLICY
    assert policy.required_approval_kinds == frozenset(
        {
            ApprovalKind.GENERATION_APPROVAL,
            ApprovalKind.PUBLISH_APPROVAL,
        }
    )
    assert len(policy.required_approval_kinds) == 2
    assert human_evidence_required(policy) is True


def test_controlled_includes_standard_approvals_and_keeps_strict_invariants() -> None:
    policy = resolve_workflow_policy("controlled")
    assert policy is CONTROLLED_POLICY
    assert ApprovalKind.GENERATION_APPROVAL in policy.required_approval_kinds
    assert ApprovalKind.PUBLISH_APPROVAL in policy.required_approval_kinds
    assert STANDARD_POLICY.required_approval_kinds <= policy.required_approval_kinds
    assert policy.hash_binding_required is True
    assert policy.author_reviewer_separation_required is True
    assert policy.immutable_audit_log_required is True
    assert human_evidence_required(policy) is True


def test_executor_automated_only_under_controlled_with_complete_evidence() -> None:
    evidence = _complete_evidence()

    for mode in ("rapid", "standard", WorkflowMode.RAPID, WorkflowMode.STANDARD):
        assert executor_automated_permitted(mode, evidence) is False
        with pytest.raises(ModeEnforcementError, match="controlled"):
            assert_executor_automated_permitted(mode, evidence)

    with pytest.raises(ModeEnforcementError, match="complete Controlled evidence"):
        assert_executor_automated_permitted("controlled", None)
    with pytest.raises(ModeEnforcementError, match="complete Controlled evidence"):
        assert_executor_automated_permitted(
            "controlled",
            ExecutorAutomatedEvidence(True, True, True, False),
        )

    allowed = assert_executor_automated_permitted("controlled", evidence)
    assert allowed.mode is WorkflowMode.CONTROLLED
    assert allowed.hash_binding_required is True
    assert allowed.author_reviewer_separation_required is True
    assert executor_automated_permitted("controlled", evidence) is True


def test_capability_downgrade_mid_run_forbidden_for_standard_and_controlled() -> None:
    assert resolve_workflow_policy("standard").allows_capability_downgrade_mid_run is False
    assert resolve_workflow_policy("controlled").allows_capability_downgrade_mid_run is False
    # Rapid also freezes mid-run capability; documented for completeness.
    assert resolve_workflow_policy("rapid").allows_capability_downgrade_mid_run is False
