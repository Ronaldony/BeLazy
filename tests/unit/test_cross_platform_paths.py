"""Cross-platform path unit tests for frozen-index, relative artifacts, ZIP members."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from video_factory.distribution import (
    DistributionPathError,
    map_archive_member_to_extract_relative,
    normalize_relative_posix,
    reject_parent_escape,
    require_relative_artifact_path,
    require_vendor_core_path,
)
from video_factory.domain import RelativeArtifactPath
from video_factory.storage import (
    FrozenIndexEntry,
    FrozenIndexViolation,
    assert_write_allowed,
    frozen_index_from_entries,
    normalize_frozen_path,
)


def test_normalize_frozen_path_mixed_separators_and_case() -> None:
    expected = normalize_frozen_path("Records/Locked.txt")
    for candidate in (
        "Records/Locked.txt",
        r"Records\Locked.txt",
        "records/locked.txt",
        "./Records/Locked.txt",
        r".\Records\Locked.txt",
        "Records//Locked.txt",
        "/Records/Locked.txt",
    ):
        assert normalize_frozen_path(candidate) == expected


def test_normalize_relative_posix_preserves_case() -> None:
    assert normalize_relative_posix(r"Vendor\Core\Pkg.whl") == "Vendor/Core/Pkg.whl"
    assert normalize_relative_posix("./vendor/core/x") == "vendor/core/x"


def test_require_relative_rejects_parent_and_absolute() -> None:
    parent = ".."
    with pytest.raises(DistributionPathError):
        require_relative_artifact_path(parent + "/outside.txt")
    with pytest.raises(DistributionPathError):
        require_relative_artifact_path("a/" + parent + "/" + parent + "/b")
    with pytest.raises(DistributionPathError):
        require_relative_artifact_path("/abs/path")
    # Construct Windows absolute without embedding a host-specific username.
    win_abs = "C:" + "\\" + "Temp" + "\\" + "file.txt"
    with pytest.raises(DistributionPathError):
        require_relative_artifact_path(win_abs)
    ok = require_relative_artifact_path(r"artifacts\results\out.bin")
    assert ok == "artifacts/results/out.bin"
    # RelativeArtifactPath is a NewType; contract is the validated string form.
    typed = RelativeArtifactPath(ok)
    assert str(typed) == "artifacts/results/out.bin"


def test_reject_parent_escape_explicit() -> None:
    reject_parent_escape("vendor/core/0.1.0/pkg.whl")  # must not raise
    parent = ".."
    with pytest.raises(DistributionPathError):
        reject_parent_escape("vendor/core/" + parent + "/secret")


def test_require_vendor_core_path_containment() -> None:
    path = require_vendor_core_path("vendor/core/0.1.0/pkg.whl", version="0.1.0")
    assert path == "vendor/core/0.1.0/pkg.whl"
    with pytest.raises(DistributionPathError):
        require_vendor_core_path("vendor/other/0.1.0/pkg.whl")
    with pytest.raises(DistributionPathError):
        require_vendor_core_path("vendor/core/0.2.0/pkg.whl", version="0.1.0")


def test_zip_member_posix_to_portable_relative() -> None:
    """ZIP always stores POSIX members; extraction must refuse zip-slip."""

    member = "brand/assets/sheet.png"
    relative = map_archive_member_to_extract_relative(member, extract_root_style="posix")
    assert relative == "brand/assets/sheet.png"
    # Windows extract root style still yields portable relative key.
    relative_win = map_archive_member_to_extract_relative(member, extract_root_style="windows")
    assert relative_win == "brand/assets/sheet.png"
    parent = ".."
    with pytest.raises(DistributionPathError):
        map_archive_member_to_extract_relative(parent + "/escape.txt")
    with pytest.raises(DistributionPathError):
        map_archive_member_to_extract_relative("/abs/member.txt")
    with pytest.raises(DistributionPathError):
        map_archive_member_to_extract_relative("good/" + parent + "/" + parent + "/evil.txt")


def test_zip_member_join_does_not_escape_extract_root() -> None:
    """Simulate extract join: archive POSIX path under a logical root."""

    member = PurePosixPath("episodes/2026-001/brief.md")
    # Logical extract root expressed as relative segments only (no host paths).
    root_segments = ("export_root",)
    joined = PurePosixPath(*root_segments, *member.parts)
    assert ".." not in joined.parts
    assert joined.as_posix() == "export_root/episodes/2026-001/brief.md"
    assert map_archive_member_to_extract_relative(member.as_posix()) == member.as_posix()


def test_frozen_index_blocks_normalized_windows_style_path() -> None:
    index = frozen_index_from_entries(
        (
            FrozenIndexEntry(
                path="history/frozen.json",
                sha256="a" * 64,
                status="historical",
            ),
        )
    )
    with pytest.raises(FrozenIndexViolation):
        assert_write_allowed(r"history\frozen.json", index)
    with pytest.raises(FrozenIndexViolation):
        assert_write_allowed("History/Frozen.json", index)
    assert_write_allowed(r"history\other.json", index)


@pytest.mark.windows
def test_windows_pathlib_as_posix_for_relative_contract() -> None:
    """Windows-only: Path with backslashes still normalizes via as_posix()."""

    if os.name != "nt":
        pytest.skip("requires Windows path semantics")
    # Relative Path on Windows accepts backslash input.
    p = Path("vendor") / "core" / "0.1.0" / "pkg.whl"
    assert "\\" in str(p) or "/" in str(p)
    assert normalize_frozen_path(p) == "vendor/core/0.1.0/pkg.whl"
    assert require_relative_artifact_path(p) == "vendor/core/0.1.0/pkg.whl"
    # PureWindowsPath mirrors ZIP→Windows extract key conversion.
    win = PureWindowsPath("brand") / "assets" / "a.png"
    assert map_archive_member_to_extract_relative(win.as_posix()) == "brand/assets/a.png"


@pytest.mark.windows
def test_windows_mixed_separator_lock_path_normalizes() -> None:
    if os.name != "nt":
        pytest.skip("requires Windows path semantics")
    mixed = "vendor\\core\\0.1.0\\video_production_core-0.1.0-py3-none-any.whl"
    assert require_vendor_core_path(mixed, version="0.1.0") == (
        "vendor/core/0.1.0/video_production_core-0.1.0-py3-none-any.whl"
    )

