"""Live-health observations: report local environment facts without asserting them.

OD-005 leaves media-tool availability unresolved. These tests may observe
whether tools exist on PATH, but they must not fail because a tool is missing
or present. Structural assertions (types, field names) are allowed.
"""

from __future__ import annotations

import shutil

import pytest

from video_factory.cli import handle_doctor, run_doctor


@pytest.mark.live_health
def test_doctor_payload_structure_is_stable_regardless_of_host() -> None:
    """Doctor must always return the same field set; values may vary by host."""

    report = run_doctor()
    result = handle_doctor()
    assert result.payload is not None
    # Structure only — do not hard-code purity exit codes or Python versions.
    for field in (
        "core_version",
        "core_contract",
        "python_version",
        "purity_exit_code",
        "purity_summary",
        "purity_available",
        # Additive diagnostics (P18); values remain host-dependent where applicable.
        "schema_registry",
        "optional_tools",
        "package_contracts",
    ):
        assert field in result.payload
    assert isinstance(report.python_version, str) and report.python_version
    assert isinstance(report.purity_available, bool)
    assert isinstance(report.purity_exit_code, int)
    assert isinstance(report.purity_summary, str)
    tools = result.payload["optional_tools"]
    assert isinstance(tools, dict)
    for name in ("ffmpeg", "ffprobe"):
        assert name in tools
        assert tools[name]["status"] in {"present", "absent"}
    # Intentionally no assert that tools are present — OD-005 remains open.


@pytest.mark.live_health
def test_media_tool_presence_is_observed_not_asserted() -> None:
    """Honest observation of ffmpeg/ffprobe — presence is never required here."""

    observations = {
        name: shutil.which(name) is not None for name in ("ffmpeg", "ffprobe")
    }
    # Only structural guarantees: keys exist and values are booleans.
    assert set(observations) == {"ffmpeg", "ffprobe"}
    assert all(isinstance(value, bool) for value in observations.values())
    # Intentionally no assert on True/False — OD-005 remains open.
