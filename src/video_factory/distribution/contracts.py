"""Distribution lock and wheel-plan contracts (ADR-001).

These types describe release artifacts and lock documents. They never write
files or launch build processes — callers materialize plans outside the core.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


LOCK_FORMAT_VERSION = 1
DEFAULT_SELECTED_DISTRIBUTION = "video-production-core"
VENDOR_CORE_PREFIX = "vendor/core/"


class ArtifactRole(StrEnum):
    """Role of a locked wheel artifact."""

    CORE = "core"
    DEPENDENCY = "dependency"


class LockVerdictStatus(StrEnum):
    """Outcome of comparing an installed core against a lock document."""

    COMPATIBLE = "compatible"
    UPGRADE_AVAILABLE = "upgrade_available"
    BREAKING = "breaking"
    PYTHON_MISMATCH = "python_mismatch"
    INVALID_LOCK = "invalid_lock"


class WheelPlanStatus(StrEnum):
    PLANNED = "planned"
    REJECTED_INVALID = "rejected_invalid"


@dataclass(frozen=True, slots=True)
class LockArtifact:
    """One ``[[artifacts]]`` table from ADR-001 ``core.lock``."""

    role: ArtifactRole
    name: str
    version: str
    path: str
    sha256: str
    contract_version: str | None = None
    source_commit: str | None = None
    requires_python: str | None = None


@dataclass(frozen=True, slots=True)
class CoreLockDocument:
    """Parsed or to-be-serialized ``core.lock`` document (lock_format=1)."""

    lock_format: int
    selected_distribution: str
    artifacts: tuple[LockArtifact, ...]

    def core_artifact(self) -> LockArtifact | None:
        """Return the sole core-role artifact, if present."""

        for artifact in self.artifacts:
            if artifact.role is ArtifactRole.CORE:
                return artifact
        return None


@dataclass(frozen=True, slots=True)
class LockVerdict:
    """Observation-only compatibility judgment (never installs or upgrades)."""

    status: LockVerdictStatus
    reason: str
    lock_core_version: str | None
    lock_contract_version: str | None
    lock_core_path: str | None
    installed_version: str
    installed_contract: str
    # Semantics: as long as a lock pins a version, a newer installed core is not
    # forced onto the channel workspace — the lock remains the authority pin.
    lock_pins_selected_version: bool
    forces_newer_install: bool


@dataclass(frozen=True, slots=True)
class WheelBuildPlan:
    """Human-executable wheel build / vendor placement plan (never executed)."""

    status: WheelPlanStatus
    package_name: str
    version: str
    argv: tuple[str, ...]
    command_string: str
    expected_wheel_filename: str
    dist_relative_path: str
    vendor_relative_path: str
    placement_notes: tuple[str, ...]
    executed: bool = False
    rejection_reason: str | None = None
