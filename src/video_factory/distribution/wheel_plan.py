"""Wheel build plan generator — command strings only (encode-builder pattern)."""

from __future__ import annotations

import shlex

from .contracts import VENDOR_CORE_PREFIX, WheelBuildPlan, WheelPlanStatus
from .lock import default_wheel_filename
from .paths import DistributionPathError, require_semver


def plan_wheel_build(
    version: str,
    *,
    package_name: str = "video-production-core",
    python: str = "python",
    dist_dir: str = "dist",
    wheel_filename: str | None = None,
) -> WheelBuildPlan:
    """Return a human-runnable wheel build + vendor placement plan.

    Never invokes ``python -m build`` or copies files. ``executed`` is always
    False. Placement notes describe ADR-001 offline vendor rules.
    """

    try:
        version_n = require_semver(version)
    except DistributionPathError as error:
        return WheelBuildPlan(
            status=WheelPlanStatus.REJECTED_INVALID,
            package_name=package_name,
            version=version,
            argv=(),
            command_string="",
            expected_wheel_filename="",
            dist_relative_path="",
            vendor_relative_path="",
            placement_notes=(),
            executed=False,
            rejection_reason=str(error),
        )

    name = package_name.strip()
    if not name:
        return WheelBuildPlan(
            status=WheelPlanStatus.REJECTED_INVALID,
            package_name=package_name,
            version=version_n,
            argv=(),
            command_string="",
            expected_wheel_filename="",
            dist_relative_path="",
            vendor_relative_path="",
            placement_notes=(),
            executed=False,
            rejection_reason="package_name must be non-empty",
        )

    filename = wheel_filename or default_wheel_filename(name, version_n)
    dist_relative = f"{dist_dir.rstrip('/')}/{filename}"
    vendor_relative = f"{VENDOR_CORE_PREFIX}{version_n}/{filename}"
    argv = (python, "-m", "build", "--wheel", "--outdir", dist_dir)
    command_string = " ".join(shlex.quote(part) for part in argv)
    notes = (
        "Run the build command from a clean core-repo commit (release tag recommended).",
        f"Copy {dist_relative} to channel repo {vendor_relative} without overwriting an existing same-version wheel.",
        "Verify SHA-256 of the wheel before and after copy (OneDrive: retry up to 3 times on lock).",
        "Install offline only: python -m pip install --no-index --find-links vendor/core/<version> <package>==<version>.",
        "Rewrite core.lock in a single commit only after contract + channel compatibility tests pass.",
        "Rollback: restore previous core.lock commit and reinstall the preserved older wheel; do not delete new wheels.",
    )
    return WheelBuildPlan(
        status=WheelPlanStatus.PLANNED,
        package_name=name,
        version=version_n,
        argv=argv,
        command_string=command_string,
        expected_wheel_filename=filename,
        dist_relative_path=dist_relative,
        vendor_relative_path=vendor_relative,
        placement_notes=notes,
        executed=False,
        rejection_reason=None,
    )
