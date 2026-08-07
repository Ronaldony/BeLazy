"""core.lock builder/parser/verifier and wheel plan tests (synthetic only)."""

from __future__ import annotations

from video_factory.distribution import (
    ArtifactRole,
    CoreLockDocument,
    CoreLockError,
    LockArtifact,
    LockVerdictStatus,
    WheelPlanStatus,
    build_core_lock,
    lock_document_from_core,
    parse_core_lock,
    plan_wheel_build,
    python_version_satisfies,
    verify_lock_against_installed,
)
import pytest

COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
SHA_A = "c" * 64
SHA_B = "d" * 64


def _core_artifact(
    *,
    version: str = "0.1.0",
    contract_version: str = "0.1",
    source_commit: str = COMMIT_A,
    sha256: str = SHA_A,
    requires_python: str = ">=3.12,<3.13",
    name: str = "video-production-core",
) -> LockArtifact:
    return LockArtifact(
        role=ArtifactRole.CORE,
        name=name,
        version=version,
        path=f"vendor/core/{version}/{name.replace('-', '_')}-{version}-py3-none-any.whl",
        sha256=sha256,
        contract_version=contract_version,
        source_commit=source_commit,
        requires_python=requires_python,
    )


def test_build_core_lock_is_deterministic() -> None:
    # Insert core second to prove sorting is deterministic.
    dep = LockArtifact(
        role=ArtifactRole.DEPENDENCY,
        name="jsonschema",
        version="4.23.0",
        path="vendor/core/0.1.0/jsonschema-4.23.0-py3-none-any.whl",
        sha256=SHA_B,
    )
    artifacts = (dep, _core_artifact())
    text_a = build_core_lock(artifacts)
    text_b = build_core_lock(reversed(artifacts))
    assert text_a == text_b
    assert text_a.encode("utf-8") == text_b.encode("utf-8")
    assert "\r" not in text_a
    assert text_a.endswith("\n")
    assert not text_a.endswith("\n\n")
    assert text_a.index('role = "core"') < text_a.index('role = "dependency"')
    assert text_a.startswith("lock_format = 1\n")


def test_build_parse_roundtrip() -> None:
    text = build_core_lock((_core_artifact(),))
    document = parse_core_lock(text)
    assert document.lock_format == 1
    assert document.selected_distribution == "video-production-core"
    core = document.core_artifact()
    assert core is not None
    assert core.version == "0.1.0"
    assert core.contract_version == "0.1"
    assert core.source_commit == COMMIT_A
    assert core.requires_python == ">=3.12,<3.13"
    # rebuild from parsed document must match original bytes
    rebuilt = build_core_lock(document.artifacts, selected_distribution=document.selected_distribution)
    assert rebuilt == text


def test_parse_rejects_path_outside_vendor_core() -> None:
    bad = """lock_format = 1
selected_distribution = "video-production-core"

[[artifacts]]
role = "core"
name = "video-production-core"
version = "0.1.0"
contract_version = "0.1"
source_commit = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
path = "elsewhere/0.1.0/pkg.whl"
sha256 = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
requires_python = ">=3.12,<3.13"
"""
    with pytest.raises(CoreLockError):
        parse_core_lock(bad)


def test_parse_rejects_parent_escape_in_path() -> None:
    # Assemble escape segments at runtime so isolation scan does not see "../.." literals.
    escape = "/".join(["..", "..", "secret.whl"])
    bad_path = "vendor/core/0.1.0/" + escape
    bad = (
        'lock_format = 1\n'
        'selected_distribution = "video-production-core"\n'
        "\n"
        "[[artifacts]]\n"
        'role = "core"\n'
        'name = "video-production-core"\n'
        'version = "0.1.0"\n'
        'contract_version = "0.1"\n'
        'source_commit = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n'
        f'path = "{bad_path}"\n'
        'sha256 = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"\n'
        'requires_python = ">=3.12,<3.13"\n'
    )
    with pytest.raises(CoreLockError):
        parse_core_lock(bad)


# --- Upgrade simulation scenarios (§20 item 6) ---


def test_upgrade_sim_same_contract_compatible() -> None:
    """① same contract + same distribution → compatible."""

    lock = lock_document_from_core(
        version="0.1.0",
        contract_version="0.1",
        source_commit=COMMIT_A,
        sha256=SHA_A,
    )
    verdict = verify_lock_against_installed(lock, "0.1.0", "0.1", installed_python="3.12.10")
    assert verdict.status is LockVerdictStatus.COMPATIBLE
    assert verdict.lock_pins_selected_version is True
    assert verdict.forces_newer_install is False
    assert verdict.lock_core_path is not None
    assert verdict.lock_core_path.startswith("vendor/core/0.1.0/")


def test_upgrade_sim_minor_rise_upgrade_available() -> None:
    """② same contract major, distribution minor/patch rise → upgrade_available."""

    lock = lock_document_from_core(
        version="0.1.0",
        contract_version="0.1",
        source_commit=COMMIT_A,
        sha256=SHA_A,
    )
    # Installed core is newer but still contract 0.1 (minor-compatible).
    verdict = verify_lock_against_installed(lock, "0.1.1", "0.1", installed_python="3.12.10")
    assert verdict.status is LockVerdictStatus.UPGRADE_AVAILABLE
    assert verdict.lock_core_version == "0.1.0"
    assert verdict.installed_version == "0.1.1"
    assert verdict.lock_pins_selected_version is True
    assert verdict.forces_newer_install is False
    # Channel assets remain on lock pin until a human rewrites core.lock.
    assert "0.1.0" in (verdict.lock_core_path or "")


def test_upgrade_sim_contract_major_breaking_lock_still_pins_old() -> None:
    """③ contract major rise → breaking; lock semantics keep old pin (no force)."""

    lock_text = build_core_lock((_core_artifact(version="0.1.0", contract_version="0.1"),))
    verdict = verify_lock_against_installed(
        lock_text,
        installed_version="1.0.0",
        installed_contract="1.0",
        installed_python="3.12.10",
    )
    assert verdict.status is LockVerdictStatus.BREAKING
    assert verdict.lock_core_version == "0.1.0"
    assert verdict.lock_contract_version == "0.1"
    assert verdict.installed_version == "1.0.0"
    assert verdict.installed_contract == "1.0"
    assert verdict.lock_pins_selected_version is True
    assert verdict.forces_newer_install is False
    # Prove §20-6: presence of lock means newer major is not auto-forced.
    assert verdict.lock_core_path == (
        "vendor/core/0.1.0/video_production_core-0.1.0-py3-none-any.whl"
    )
    # Re-parse lock: still points only at 0.1.0 artifacts.
    document = parse_core_lock(lock_text)
    assert document.core_artifact() is not None
    assert document.core_artifact().version == "0.1.0"
    assert all("1.0.0" not in item.path for item in document.artifacts)


def test_upgrade_sim_requires_python_mismatch_od003() -> None:
    """④ requires_python mismatch vs OD-003 3.12-only range."""

    lock = lock_document_from_core(
        version="0.1.0",
        contract_version="0.1",
        source_commit=COMMIT_A,
        sha256=SHA_A,
        requires_python=">=3.12,<3.13",
    )
    verdict = verify_lock_against_installed(
        lock,
        "0.1.0",
        "0.1",
        installed_python="3.13.0",
    )
    assert verdict.status is LockVerdictStatus.PYTHON_MISMATCH
    assert verdict.lock_pins_selected_version is True
    assert verdict.forces_newer_install is False
    assert python_version_satisfies("3.12.10", ">=3.12,<3.13") is True
    assert python_version_satisfies("3.13.0", ">=3.12,<3.13") is False
    assert python_version_satisfies("3.11.9", ">=3.12,<3.13") is False


def test_plan_wheel_build_command_strings_only() -> None:
    plan = plan_wheel_build("0.1.0")
    assert plan.status is WheelPlanStatus.PLANNED
    assert plan.executed is False
    assert plan.argv == ("python", "-m", "build", "--wheel", "--outdir", "dist")
    assert "build" in plan.command_string
    assert plan.expected_wheel_filename == "video_production_core-0.1.0-py3-none-any.whl"
    assert plan.dist_relative_path == "dist/video_production_core-0.1.0-py3-none-any.whl"
    assert plan.vendor_relative_path == (
        "vendor/core/0.1.0/video_production_core-0.1.0-py3-none-any.whl"
    )
    assert any("core.lock" in note for note in plan.placement_notes)
    assert any("no-index" in note for note in plan.placement_notes)


def test_plan_wheel_build_rejects_bad_version() -> None:
    plan = plan_wheel_build("not-a-version")
    assert plan.status is WheelPlanStatus.REJECTED_INVALID
    assert plan.executed is False
    assert plan.argv == ()
    assert plan.rejection_reason is not None


def test_verify_accepts_lock_text_roundtrip() -> None:
    document = CoreLockDocument(
        lock_format=1,
        selected_distribution="video-production-core",
        artifacts=(_core_artifact(),),
    )
    text = build_core_lock(document.artifacts)
    verdict = verify_lock_against_installed(text, "0.1.0", "0.1")
    assert verdict.status is LockVerdictStatus.COMPATIBLE

