from __future__ import annotations

import os
from pathlib import Path

import pytest

import tools.check_target_boundary as target_boundary
from tools.check_target_boundary import (
    canonical_component,
    forbidden_path_rule,
    scan_target,
)


ROOT = Path(__file__).resolve().parents[2]


def test_target_boundary_accepts_current_repository() -> None:
    report = scan_target(ROOT)

    assert report.violations == ()
    assert report.scanned_files > 0
    assert report.scanned_directories > 0


def test_target_boundary_rejects_nested_git_and_generated_material(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("ignored root metadata", encoding="utf-8")
    (tmp_path / "nested" / ".git").mkdir(parents=True)
    (tmp_path / "nested" / ".git" / "config").write_text("nested", encoding="utf-8")
    (tmp_path / ".pytest_cache").mkdir()
    (tmp_path / ".pytest_cache" / "state").write_text("generated", encoding="utf-8")

    report = scan_target(tmp_path)

    assert {item.rule_id for item in report.violations} == {
        "forbidden_generated_directory",
        "nested_git_metadata",
    }


def test_target_boundary_requires_root_git_to_be_a_local_directory(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").write_text("external gitdir indirection", encoding="utf-8")

    report = scan_target(tmp_path)

    assert [(item.rule_id, item.relative_path) for item in report.violations] == [
        ("root_git_not_local_directory", ".git")
    ]


def test_target_boundary_rejects_symlink_without_following_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outside = tmp_path / "ordinary-target.txt"
    outside.write_text("ordinary", encoding="utf-8")
    link = tmp_path / "linked.txt"
    try:
        os.symlink(outside, link)
    except OSError:
        link.write_text("synthetic reparse entry", encoding="utf-8")
        original = target_boundary._is_reparse
        monkeypatch.setattr(
            target_boundary,
            "_is_reparse",
            lambda entry: entry.name == link.name or original(entry),
        )

    report = scan_target(tmp_path)

    assert [(item.rule_id, item.relative_path) for item in report.violations] == [
        ("reparse_or_symlink", "linked.txt")
    ]


def test_target_boundary_uses_casefolded_nfc_collision_keys() -> None:
    assert canonical_component("Schema.JSON") == canonical_component("schema.json")
    assert canonical_component("e\u0301.json") == canonical_component("\u00e9.json")


@pytest.mark.parametrize(
    ("relative", "is_directory", "expected"),
    [
        (Path("nested/.git"), True, "nested_git_metadata"),
        (Path("dist"), True, "forbidden_generated_directory"),
        (Path("package.egg-info"), True, "forbidden_generated_directory"),
        (Path("module.pyc"), False, "forbidden_generated_file"),
        (Path(".env.local"), False, "forbidden_generated_file"),
    ],
)
def test_target_boundary_classifies_forbidden_paths(
    relative: Path,
    is_directory: bool,
    expected: str,
) -> None:
    assert forbidden_path_rule(relative, is_directory=is_directory) == expected
