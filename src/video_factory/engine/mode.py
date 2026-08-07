"""Fail-safe execution-mode intersection used at the config boundary."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import ExecutionMode


_MODE_RANK = {
    ExecutionMode.PREVIEW_ONLY: 0,
    ExecutionMode.HUMAN_ONLY: 1,
    ExecutionMode.AUTOMATED: 2,
}


@dataclass(frozen=True, slots=True)
class ExecutionModeLimits:
    """Channel, selected workflow mode, and adapter maxima from ADR-004."""

    channel_maximum: ExecutionMode | str | None = None
    mode_maximum: ExecutionMode | str | None = None
    adapter_maximum: ExecutionMode | str | None = None


@dataclass(frozen=True, slots=True)
class EffectiveExecutionMode:
    """Normalized limits and their most restrictive effective result."""

    effective_mode: ExecutionMode
    channel_maximum: ExecutionMode
    mode_maximum: ExecutionMode
    adapter_maximum: ExecutionMode
    fail_safe_sources: tuple[str, ...]


def _normalize_limit(
    value: ExecutionMode | str | None,
    source: str,
) -> tuple[ExecutionMode, str | None]:
    if isinstance(value, ExecutionMode):
        return value, None
    if isinstance(value, str):
        try:
            return ExecutionMode(value), None
        except ValueError:
            pass
    return ExecutionMode.HUMAN_ONLY, source


def resolve_effective_execution_mode(limits: ExecutionModeLimits) -> EffectiveExecutionMode:
    """Intersect all three maxima; missing or unknown values fail safe to human-only."""

    normalized: list[ExecutionMode] = []
    fail_safe_sources: list[str] = []
    for source, value in (
        ("channel", limits.channel_maximum),
        ("mode", limits.mode_maximum),
        ("adapter", limits.adapter_maximum),
    ):
        mode, failed_source = _normalize_limit(value, source)
        normalized.append(mode)
        if failed_source is not None:
            fail_safe_sources.append(failed_source)

    effective = min(normalized, key=_MODE_RANK.__getitem__)
    return EffectiveExecutionMode(
        effective_mode=effective,
        channel_maximum=normalized[0],
        mode_maximum=normalized[1],
        adapter_maximum=normalized[2],
        fail_safe_sources=tuple(fail_safe_sources),
    )


def mode_is_within_limit(mode: ExecutionMode, maximum: ExecutionMode) -> bool:
    """Return whether a requested mode is no less restrictive than a maximum."""

    return _MODE_RANK[mode] <= _MODE_RANK[maximum]
