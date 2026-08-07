"""CLI command specification types.

A CommandSpec answers three operational questions without claiming runtime power
the core does not yet have:

1. What is the command's public name and purpose?
2. Does the command require an explicit workflow mode (OD-004 fail-closed)?
3. Is the command fully implemented, contract-only, or not yet backed?

Keeping status on the registry prevents a silent stub from looking like a
working operational surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ImplementationStatus(StrEnum):
    """Honesty marker for registry entries (no false-working stubs)."""

    IMPLEMENTED = "implemented"
    CONTRACT_ONLY = "contract_only"
    NOT_YET_BACKED = "not_yet_backed"


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """One registered CLI command and its current backing reality."""

    name: str
    summary: str
    requires_workflow_mode: bool
    implementation_status: ImplementationStatus
    # Core package dotted names this command actually calls when implemented or
    # when evaluating a contract. Empty for pure placeholders.
    backing_modules: tuple[str, ...]
    # Human-readable phase hint for not_yet_backed / contract_only entries.
    expected_backing: str | None = None
