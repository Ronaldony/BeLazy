"""Resolve a caller-supplied workflow mode string to its WorkflowPolicy."""

from __future__ import annotations

from video_factory.engine.contracts import WorkflowMode

from .catalog import WORKFLOW_POLICIES
from .models import WorkflowPolicy, WorkflowPolicyError


def parse_workflow_mode(mode: str | WorkflowMode | None) -> WorkflowMode:
    """Parse an explicit mode. None or unknown values fail closed (OD-004).

    Unlike adapter execution mode (ADR-004), which fails safe to human_only when
    missing, workflow mode has no production default in the core. Callers must
    supply a recognized mode; silence is rejection, not Standard.
    """

    if mode is None:
        raise WorkflowPolicyError(
            "workflow mode is required; the core does not choose a production default"
        )
    if isinstance(mode, WorkflowMode):
        return mode
    if not isinstance(mode, str) or not mode.strip():
        raise WorkflowPolicyError(
            "workflow mode is required; the core does not choose a production default"
        )
    try:
        return WorkflowMode(mode.strip().lower())
    except ValueError as error:
        raise WorkflowPolicyError(
            f"unknown workflow mode {mode!r}; expected one of "
            f"{', '.join(item.value for item in WorkflowMode)}"
        ) from error


def resolve_workflow_policy(mode: str | WorkflowMode | None) -> WorkflowPolicy:
    """Return the fixed policy definition for an explicit workflow mode.

    Never falls back to Standard or any other mode when the argument is missing
    or unparsable (OD-004 open decision).
    """

    parsed = parse_workflow_mode(mode)
    return WORKFLOW_POLICIES[parsed]
