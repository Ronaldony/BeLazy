"""Workflow state contracts and lazy plan-only orchestration exports.

Orchestration depends on workflow policy, while policy depends on the engine
enums. Lazy exports keep either package independently importable.
"""

from __future__ import annotations

from importlib import import_module

from .contracts import (
    ExecutionMode,
    TransitionRequest,
    TransitionResult,
    WorkflowEngine,
    WorkflowMode,
    WorkflowState,
)
from .mode import (
    EffectiveExecutionMode,
    ExecutionModeLimits,
    mode_is_within_limit,
    resolve_effective_execution_mode,
)

_ARTIFACT_GRAPH_EXPORTS = frozenset(
    {
        "ArtifactGraph",
        "ArtifactGraphError",
        "ArtifactGraphFinding",
        "ArtifactSnapshot",
        "artifact_reference_to_mapping",
        "bound_reference_from_mapping",
        "build_artifact_graph",
        "make_artifact_snapshot",
        "make_artifact_snapshot_from_json_bytes",
        "snapshot_from_mapping",
    }
)
_ORCHESTRATION_EXPORTS = frozenset(
    {
        "EpisodeStateObservation",
        "GenerationReadinessPlan",
        "NextStepPlan",
        "ObservedArtifact",
        "OrchestrationPlanError",
        "PipelineKind",
        "build_generation_readiness",
        "next_step_to_mapping",
        "observation_to_mapping",
        "observe_episode_state",
        "plan_next_step",
    }
)


def __getattr__(name: str) -> object:
    if name in _ARTIFACT_GRAPH_EXPORTS:
        module = import_module(".artifact_graph", __name__)
    elif name in _ORCHESTRATION_EXPORTS:
        module = import_module(".orchestration", __name__)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | _ARTIFACT_GRAPH_EXPORTS | _ORCHESTRATION_EXPORTS)


__all__ = [
    "ArtifactGraph",
    "ArtifactGraphError",
    "ArtifactGraphFinding",
    "ArtifactSnapshot",
    "EffectiveExecutionMode",
    "EpisodeStateObservation",
    "ExecutionMode",
    "ExecutionModeLimits",
    "GenerationReadinessPlan",
    "NextStepPlan",
    "ObservedArtifact",
    "OrchestrationPlanError",
    "PipelineKind",
    "TransitionRequest",
    "TransitionResult",
    "WorkflowEngine",
    "WorkflowMode",
    "WorkflowState",
    "artifact_reference_to_mapping",
    "bound_reference_from_mapping",
    "build_artifact_graph",
    "build_generation_readiness",
    "make_artifact_snapshot",
    "make_artifact_snapshot_from_json_bytes",
    "mode_is_within_limit",
    "next_step_to_mapping",
    "observation_to_mapping",
    "observe_episode_state",
    "plan_next_step",
    "resolve_effective_execution_mode",
    "snapshot_from_mapping",
]
