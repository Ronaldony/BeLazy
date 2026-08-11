"""Compatibility exports for fail-safe execution-mode intersection."""

from __future__ import annotations

from video_factory._mode_contracts import (
    EffectiveExecutionMode,
    ExecutionMode,
    ExecutionModeLimits,
    mode_is_within_limit,
    resolve_effective_execution_mode,
)


__all__ = [
    "EffectiveExecutionMode",
    "ExecutionMode",
    "ExecutionModeLimits",
    "mode_is_within_limit",
    "resolve_effective_execution_mode",
]
