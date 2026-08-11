"""Bridge ADR-006 workflow policy to ADR-004 adapter enforcement points.

Workflow mode and execution mode remain separate axes:
- workflow mode → approval / review / audit strictness (this package)
- execution mode → whether adapters may auto-dispatch externally (engine + providers)
"""

from __future__ import annotations

from video_factory._mode_contracts import (
    ExecutionMode,
    ModeEnforcementError,
    WorkflowMode,
)

from .models import (
    ExecutorAutomatedEvidence,
    WorkflowPolicy,
    WorkflowPolicyError,
)
from .resolve import resolve_workflow_policy


def human_evidence_required(policy: WorkflowPolicy) -> bool:
    """Compute OrchestrationPolicy.human_evidence_required from a workflow policy.

    Rapid requires no human approval kinds (draft only), so evidence is not
    required. Standard and Controlled declare generation/publish (and more)
    approval kinds, so non-preview orchestration must present hash-bound evidence.
    OrchestrationGuard still skips the evidence check for PREVIEW_ONLY.
    """

    return bool(policy.required_approval_kinds)


def human_evidence_required_for_mode(mode: str | WorkflowMode | None) -> bool:
    """Resolve mode then compute human_evidence_required."""

    return human_evidence_required(resolve_workflow_policy(mode))


def execution_mode_ceiling(policy: WorkflowPolicy) -> ExecutionMode:
    """Return the highest adapter execution mode the workflow mode may request."""

    return policy.maximum_execution_mode


def assert_executor_automated_permitted(
    mode: str | WorkflowMode | None,
    evidence: ExecutorAutomatedEvidence | None,
) -> WorkflowPolicy:
    """Allow executor automated dispatch only under Controlled + complete evidence.

    ADR-004: automated is allowed only when Controlled MANUAL_WINDOW, valid task
    hash, daily reservation, and kill switch are all satisfied.
    ADR-006: only Controlled exposes executor automated; Rapid/Standard reject it.
    Strict Controlled invariants (hash binding, author/reviewer separation) must
    also be active on the resolved policy data.
    """

    try:
        policy = resolve_workflow_policy(mode)
    except WorkflowPolicyError as error:
        raise ModeEnforcementError(str(error)) from error

    if policy.mode is not WorkflowMode.CONTROLLED:
        raise ModeEnforcementError(
            "executor automated mode requires controlled workflow mode"
        )
    if not policy.hash_binding_required:
        raise ModeEnforcementError(
            "executor automated mode requires hash-binding on the workflow policy"
        )
    if not policy.author_reviewer_separation_required:
        raise ModeEnforcementError(
            "executor automated mode requires author/reviewer separation"
        )
    if policy.maximum_execution_mode is not ExecutionMode.AUTOMATED:
        raise ModeEnforcementError(
            "workflow policy execution ceiling does not permit automated"
        )
    if evidence is None or not evidence.is_complete:
        raise ModeEnforcementError(
            "executor automated mode requires complete Controlled evidence "
            "(manual window, task hash, reservation, kill switch)"
        )
    return policy


def executor_automated_permitted(
    mode: str | WorkflowMode | None,
    evidence: ExecutorAutomatedEvidence | None,
) -> bool:
    """Boolean form of assert_executor_automated_permitted."""

    try:
        assert_executor_automated_permitted(mode, evidence)
    except (ModeEnforcementError, WorkflowPolicyError):
        return False
    return True
