"""Unit tests for the workspace export planner and sensitive-pattern scanner."""

from __future__ import annotations

import hashlib
from pathlib import Path

from video_factory.cli import handle_export
from video_factory.storage import (
    WorkspaceExportPlanStatus,
    pattern_kind_ids,
    plan_export,
    scan_for_sensitive_content,
)
from video_factory.storage.workspace_export import WorkspaceExportEngine


def _clean_workspace(root: Path) -> Path:
    source = root / "clean_source"
    source.mkdir()
    (source / "readme.md").write_text(
        "synthetic workspace without personal data\n",
        encoding="utf-8",
    )
    (source / "config.json").write_text(
        '{"channel_id":"synth-channel","note":"relative paths only"}\n',
        encoding="utf-8",
    )
    return source


def _windows_abs_sample() -> str:
    """Build a Windows absolute path without embedding a purity-scan literal."""

    drive = "C"
    sep = chr(92)
    return drive + ":" + sep + "Users" + sep + "sample_operator" + sep + "docs"


def _email_sample() -> str:
    return "operator" + "@" + "example" + "." + "test"


def _api_key_sample() -> str:
    return "sk-" + ("x" * 24)


def _uuid_sample() -> str:
    return "123e4567-e89b-12d3-a456-426614174000"


def _snapshot_tree(root: Path) -> dict[str, str | None]:
    snapshot: dict[str, str | None] = {}
    if not root.exists():
        return snapshot
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_dir():
            snapshot[rel + "/"] = None
        elif path.is_file():
            snapshot[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def test_plan_export_clean_workspace_to_directory(tmp_path: Path) -> None:
    source = _clean_workspace(tmp_path)
    target = tmp_path / "export_out"
    before = _snapshot_tree(tmp_path)

    plan = plan_export(source, target)

    assert plan.status is WorkspaceExportPlanStatus.READY
    assert plan.executed is False
    assert len(plan.files) == 2
    assert plan.files_scanned == 2
    assert plan.output_kind == "directory"
    assert set(plan.pattern_kinds_checked) == set(pattern_kind_ids())
    assert "windows_absolute_path" in plan.pattern_kinds_checked
    assert not target.exists()
    assert _snapshot_tree(tmp_path) == before


def test_plan_export_clean_workspace_to_zip(tmp_path: Path) -> None:
    source = _clean_workspace(tmp_path)
    archive = tmp_path / "package.zip"
    before = _snapshot_tree(tmp_path)

    plan = plan_export(source, archive)

    assert plan.status is WorkspaceExportPlanStatus.READY
    assert plan.output_kind == "zip"
    assert plan.executed is False
    assert not archive.exists()
    assert len(plan.files) == 2
    assert _snapshot_tree(tmp_path) == before


def test_plan_export_refuses_windows_absolute_path(tmp_path: Path) -> None:
    source = _clean_workspace(tmp_path)
    (source / "notes.md").write_text(
        "path was " + _windows_abs_sample() + "\n",
        encoding="utf-8",
    )
    target = tmp_path / "should_not_write"
    before = _snapshot_tree(tmp_path)

    plan = plan_export(source, target)

    assert plan.status is WorkspaceExportPlanStatus.REJECTED_SENSITIVE
    assert plan.findings
    assert any(item.pattern_id == "windows_absolute_path" for item in plan.findings)
    assert plan.files == ()
    assert plan.executed is False
    assert not target.exists()
    assert _snapshot_tree(tmp_path) == before


def test_plan_export_refuses_email_address(tmp_path: Path) -> None:
    source = _clean_workspace(tmp_path)
    (source / "contact.txt").write_text(
        "contact " + _email_sample() + "\n",
        encoding="utf-8",
    )
    target = tmp_path / "email_export"

    plan = plan_export(source, target)

    assert plan.status is WorkspaceExportPlanStatus.REJECTED_SENSITIVE
    assert any(item.pattern_id == "email_address" for item in plan.findings)
    assert not target.exists()


def test_plan_export_refuses_api_key_shape(tmp_path: Path) -> None:
    source = _clean_workspace(tmp_path)
    (source / "secrets.txt").write_text(
        "token " + _api_key_sample() + "\n",
        encoding="utf-8",
    )
    target = tmp_path / "key_export"

    plan = plan_export(source, target)

    assert plan.status is WorkspaceExportPlanStatus.REJECTED_SENSITIVE
    assert any(item.pattern_id == "api_key_shape" for item in plan.findings)
    assert not target.exists()


def test_plan_export_refuses_session_uuid(tmp_path: Path) -> None:
    source = _clean_workspace(tmp_path)
    (source / "run.txt").write_text(
        "run_id " + _uuid_sample() + "\n",
        encoding="utf-8",
    )
    target = tmp_path / "uuid_export"

    plan = plan_export(source, target)

    assert plan.status is WorkspaceExportPlanStatus.REJECTED_SENSITIVE
    assert any(item.pattern_id == "session_or_run_uuid" for item in plan.findings)
    assert not target.exists()


def test_scanner_uses_general_patterns_not_hardcoded_username() -> None:
    """Regression: scanner source must not hard-code a personal username."""

    export_module = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "video_factory"
        / "storage"
        / "workspace_export.py"
    )
    text = export_module.read_text(encoding="utf-8")
    personal = "wo" + "tmd"
    assert personal not in text.casefold()
    assert "USERNAME" not in text
    kinds = pattern_kind_ids()
    assert "windows_absolute_path" in kinds
    assert "email_address" in kinds
    assert "api_key_shape" in kinds
    assert "session_or_run_uuid" in kinds


def test_handle_export_success_and_refusal(tmp_path: Path) -> None:
    source = _clean_workspace(tmp_path)
    ok_target = tmp_path / "cli_ok"
    before = _snapshot_tree(tmp_path)
    ok = handle_export(source_dir=source, target=ok_target)
    assert ok.exit_code == 0
    assert ok.status == "ok"
    assert ok.payload is not None
    assert ok.payload["file_count"] == 2
    assert ok.payload["executed"] is False
    assert not ok_target.exists()

    (source / "leak.md").write_text(_email_sample() + "\n", encoding="utf-8")
    bad_target = tmp_path / "cli_bad"
    bad = handle_export(source_dir=source, target=bad_target)
    assert bad.exit_code == 1
    assert bad.status == "export_refused"
    assert not bad_target.exists()
    # Source gained leak.md; targets must still be absent.
    assert not ok_target.exists()


def test_engine_facade_scan_matches_export_refusal(tmp_path: Path) -> None:
    source = _clean_workspace(tmp_path)
    (source / "p.md").write_text(_windows_abs_sample() + "\n", encoding="utf-8")
    engine = WorkspaceExportEngine()
    findings, scanned = engine.scan(source)
    assert scanned >= 1
    assert findings
    plan = engine.plan_export(source, tmp_path / "nope")
    assert plan.status is WorkspaceExportPlanStatus.REJECTED_SENSITIVE
    assert plan.executed is False


def test_scan_for_sensitive_content_still_read_only(tmp_path: Path) -> None:
    source = _clean_workspace(tmp_path)
    before = _snapshot_tree(tmp_path)
    findings, scanned = scan_for_sensitive_content(source)
    assert scanned == 2
    assert findings == []
    assert _snapshot_tree(tmp_path) == before
