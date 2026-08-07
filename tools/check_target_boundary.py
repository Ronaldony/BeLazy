"""Fail closed on repository paths that can escape or pollute the target.

The walk uses ``os.scandir`` and never follows symbolic links or Windows
reparse points.  The root ``.git`` directory is intentionally opaque; nested
Git metadata and generated/cache material anywhere else are violations.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
import sys
import unicodedata


FORBIDDEN_DIRECTORY_NAMES = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".venv",
        "venv",
        "build",
        "dist",
        "htmlcov",
        "runtime-workspaces",
        "generated-media",
    }
)
REPARSE_POINT_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


@dataclass(frozen=True, slots=True)
class BoundaryViolation:
    rule_id: str
    relative_path: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class BoundaryReport:
    violations: tuple[BoundaryViolation, ...]
    scanned_files: int
    scanned_directories: int


def canonical_component(name: str) -> str:
    """Return the collision key used by Windows-safe target paths."""

    return unicodedata.normalize("NFC", name).casefold()


def forbidden_path_rule(relative: Path, *, is_directory: bool) -> str | None:
    """Classify forbidden generated or nested-control paths."""

    name = relative.name.casefold()
    if name == ".git":
        return "nested_git_metadata"
    if is_directory and (
        name in FORBIDDEN_DIRECTORY_NAMES or name.endswith(".egg-info")
    ):
        return "forbidden_generated_directory"
    if not is_directory and (
        name.endswith(".pyc")
        or name.startswith(".coverage")
        or name == ".env"
        or name.startswith(".env.")
    ):
        return "forbidden_generated_file"
    return None


def _is_reparse(entry: os.DirEntry[str]) -> bool:
    if entry.is_symlink():
        return True
    try:
        attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & REPARSE_POINT_FLAG)


def scan_target(root: Path) -> BoundaryReport:
    """Inspect *root* without following links and return deterministic findings."""

    root = Path(root)
    violations: list[BoundaryViolation] = []
    scanned_files = 0
    scanned_directories = 0

    try:
        root_stat = root.lstat()
    except OSError as exc:
        return BoundaryReport(
            (BoundaryViolation("target_root_unreadable", ".", type(exc).__name__),),
            0,
            0,
        )

    if root.is_symlink() or bool(
        getattr(root_stat, "st_file_attributes", 0) & REPARSE_POINT_FLAG
    ):
        return BoundaryReport((BoundaryViolation("target_root_reparse", "."),), 0, 0)

    pending = [root]
    while pending:
        current = pending.pop()
        scanned_directories += 1
        try:
            entries = sorted(
                os.scandir(current),
                key=lambda item: (canonical_component(item.name), item.name),
            )
        except OSError as exc:
            relative = current.relative_to(root).as_posix() or "."
            violations.append(
                BoundaryViolation("directory_unreadable", relative, type(exc).__name__)
            )
            continue

        collision_groups: dict[str, list[str]] = {}
        for entry in entries:
            collision_groups.setdefault(canonical_component(entry.name), []).append(entry.name)
        for names in collision_groups.values():
            if len(names) > 1:
                relative = current.relative_to(root).as_posix() or "."
                violations.append(
                    BoundaryViolation(
                        "case_or_unicode_collision",
                        relative,
                        " | ".join(sorted(names)),
                    )
                )

        for entry in entries:
            relative = Path(entry.path).relative_to(root)
            relative_text = relative.as_posix()
            if relative.parts == (".git",):
                try:
                    root_git_is_directory = entry.is_dir(follow_symlinks=False)
                except OSError:
                    root_git_is_directory = False
                if _is_reparse(entry) or not root_git_is_directory:
                    violations.append(
                        BoundaryViolation("root_git_not_local_directory", relative_text)
                    )
                continue

            if _is_reparse(entry):
                violations.append(BoundaryViolation("reparse_or_symlink", relative_text))
                continue

            try:
                is_directory = entry.is_dir(follow_symlinks=False)
                is_file = entry.is_file(follow_symlinks=False)
            except OSError as exc:
                violations.append(
                    BoundaryViolation("path_unreadable", relative_text, type(exc).__name__)
                )
                continue

            forbidden_rule = forbidden_path_rule(relative, is_directory=is_directory)
            if forbidden_rule is not None:
                violations.append(BoundaryViolation(forbidden_rule, relative_text))
                continue

            if is_directory:
                pending.append(Path(entry.path))
            elif is_file:
                scanned_files += 1
            else:
                violations.append(BoundaryViolation("non_regular_path", relative_text))

    ordered = tuple(
        sorted(
            violations,
            key=lambda item: (item.relative_path, item.rule_id, item.detail),
        )
    )
    return BoundaryReport(ordered, scanned_files, scanned_directories)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    report = scan_target(root)
    if report.violations:
        for violation in report.violations:
            detail = f" {violation.detail}" if violation.detail else ""
            print(f"FAIL {violation.rule_id} {violation.relative_path}{detail}")
        print(
            "target_boundary=FAIL "
            f"scanned_files={report.scanned_files} "
            f"scanned_directories={report.scanned_directories} "
            f"violations={len(report.violations)}"
        )
        return 1

    print(
        "target_boundary=PASS "
        f"scanned_files={report.scanned_files} "
        f"scanned_directories={report.scanned_directories} violations=0"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
