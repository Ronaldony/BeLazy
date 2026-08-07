"""Unit tests for doctor diagnostic field structure (no tool presence asserts)."""

from __future__ import annotations

import ast
from pathlib import Path

from video_factory.cli.handlers import (
    _observe_optional_tools,
    _observe_schema_registry,
    _package_contract_summary,
    handle_doctor,
    run_doctor,
)


LEGACY_DOCTOR_FIELDS = (
    "core_version",
    "core_contract",
    "python_version",
    "purity_exit_code",
    "purity_summary",
    "purity_available",
)

NEW_DOCTOR_FIELDS = (
    "schema_registry",
    "optional_tools",
    "package_contracts",
)


def test_schema_registry_observation_structure() -> None:
    obs = _observe_schema_registry()
    assert obs["available"] is True
    assert isinstance(obs["schema_files"], int)
    assert obs["schema_files"] >= 1
    assert isinstance(obs["registered_count"], int)
    assert obs["registered_count"] >= 1
    assert isinstance(obs["registered_versions"], list)
    assert isinstance(obs["load_failures"], list)
    # Structure only: integrity means list type is present; empty list is success.
    assert "analytics-record/1.0" in obs["registered_versions"]
    assert "retro-report/1.0" in obs["registered_versions"]


def test_optional_tools_observation_is_status_map_only() -> None:
    tools = _observe_optional_tools()
    assert set(tools) == {"ffmpeg", "ffprobe"}
    for name, entry in tools.items():
        assert isinstance(entry, dict)
        assert entry["status"] in {"present", "absent"}
        # Must not raise or fail based on which status is observed.
        assert "path" in entry


def test_package_contract_summary_has_versions() -> None:
    summary = _package_contract_summary()
    assert summary["core_version"]
    assert summary["core_contract"]
    assert summary["config_contract"]


def test_doctor_payload_keeps_legacy_fields_and_adds_diagnostics() -> None:
    report = run_doctor()
    result = handle_doctor()
    assert result.payload is not None
    for field in LEGACY_DOCTOR_FIELDS:
        assert field in result.payload
        assert getattr(report, field) == result.payload[field]
    for field in NEW_DOCTOR_FIELDS:
        assert field in result.payload
        assert getattr(report, field) is not None
    registry = result.payload["schema_registry"]
    assert isinstance(registry, dict)
    assert "registered_count" in registry
    assert "load_failures" in registry
    tools = result.payload["optional_tools"]
    assert set(tools) == {"ffmpeg", "ffprobe"}
    # Doctor exit code is driven by purity / schema integrity, never by tool absence.
    for entry in tools.values():
        assert entry["status"] in {"present", "absent"}
    assert report.purity_available is True
    assert "core_purity=" in report.purity_summary


def test_doctor_module_has_no_child_process_launcher() -> None:
    """doctor path must stay in-process (no child-process module import)."""

    handlers_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "video_factory"
        / "cli"
        / "handlers.py"
    )
    source = handlers_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] != "sub" + "process"
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] != "sub" + "process"

