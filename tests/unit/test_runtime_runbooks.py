from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
RUNBOOKS = ROOT / "docs" / "runbooks"


def test_w06_runbook_set_names_every_required_operation() -> None:
    expected = {
        "break-glass.md",
        "deployment.md",
        "drift.md",
        "incident.md",
        "journal-recovery.md",
        "migration.md",
        "release.md",
        "rollback.md",
    }
    assert {path.name for path in RUNBOOKS.glob("*.md")} == expected


def test_runbooks_preserve_fail_closed_runtime_and_rollback_rules() -> None:
    deployment = (RUNBOOKS / "deployment.md").read_text(encoding="utf-8")
    migration = (RUNBOOKS / "migration.md").read_text(encoding="utf-8")
    recovery = (RUNBOOKS / "journal-recovery.md").read_text(encoding="utf-8")
    release = (RUNBOOKS / "release.md").read_text(encoding="utf-8")
    rollback = (RUNBOOKS / "rollback.md").read_text(encoding="utf-8")

    assert "fixture_only=true" in deployment
    assert "production_enabled=false" in deployment
    assert "unregistered" in migration
    assert "projection_read_only" in migration
    assert "dispatching" in recovery
    assert "reconcile-only" in recovery
    assert "never blindly" in release.lower()
    assert "W05 `ready`" in release
    assert "Do not delete or edit a journal" in deployment
    assert "preserve the entire fixture directory" in deployment
    assert "Never dispatch them again" in rollback


def test_architecture_and_public_contract_document_the_split_package_boundary() -> None:
    adr = (
        ROOT / "docs" / "architecture" / "decisions" / "ADR-RUN-001.md"
    ).read_text(encoding="utf-8")
    contracts = (ROOT / "CONTRACTS.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "production activation disabled" in adr
    assert "`video_factory.runtime`" in contracts
    assert "`video_factory_runtime`" in contracts
    assert "tools/check_runtime_boundary.py" in contracts
    assert "tools/check_runtime_boundary.py" in readme


def test_deployment_wheel_command_uses_the_actual_required_cli_flags(
    tmp_path: Path,
) -> None:
    deployment = (RUNBOOKS / "deployment.md").read_text(encoding="utf-8")
    command = "python tools/verify_schema_wheel.py"
    assert command in deployment
    for flag in ("--wheel", "--python", "--work-dir"):
        assert flag in deployment

    # A nonexistent wheel reaches product validation (exit 1), rather than
    # argparse's missing-required-argument exit 2.  This keeps the runbook
    # invocation shape synchronized with the real parser without building a
    # package inside the documentation test.
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "verify_schema_wheel.py"),
            "--wheel",
            str(tmp_path / "missing.whl"),
            "--python",
            sys.executable,
            "--work-dir",
            str(tmp_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "following arguments are required" not in result.stderr
