"""Fail when core sources or tests reach outside a neutral repository tree.

This guard complements ``check_core_purity.py`` (channel / product identity).
Isolation rules focus on:

1. Absolute path literals that pin a machine or home directory
2. Concrete sibling-workspace identity strings
3. Multi-level parent-directory escape patterns (``../..``)
4. ``open()`` / ``Path()`` calls that embed such escapes (AST when possible)

Only ``src/`` and ``tests/`` are scanned. The checker itself lives under
``tools/`` and is intentionally outside the scan roots.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import re
import sys
from typing import Iterable, Pattern


SKIPPED_DIRECTORIES = frozenset(
    {".git", ".pytest_cache", ".mypy_cache", ".ruff_cache", "__pycache__", ".venv"}
)

SCAN_ROOTS = ("src", "tests")


def _joined(*parts: str) -> str:
    return "".join(parts)


@dataclass(frozen=True, slots=True)
class ScanRule:
    rule_id: str
    pattern: Pattern[str]


@dataclass(frozen=True, slots=True)
class Violation:
    rule_id: str
    relative_path: str
    line_number: int | None
    detail: str = ""


def content_rules() -> tuple[ScanRule, ...]:
    """Regex rules applied to relative paths and UTF-8 file text."""

    # Construct forbidden literals so this source file does not self-match
    # when a broader scan is ever enabled.
    sibling_workspace = _joined("Boss", "Kimu")
    win_drive = re.compile(r"(?i)\b[A-Za-z]:[\\/]")
    unix_home = re.compile(r"(?i)/(?:home|Users)/")
    parent_escape = re.compile(r"(?:\.\./){2,}|(?:\.\.\\){2,}")
    return (
        ScanRule("sibling_workspace_identity", re.compile(re.escape(sibling_workspace))),
        ScanRule("windows_absolute_path", win_drive),
        ScanRule("unix_home_absolute_path", unix_home),
        ScanRule("parent_directory_escape", parent_escape),
    )


def _string_literals_from_call(node: ast.Call) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for arg in node.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            found.append((arg.lineno, arg.value))
        elif isinstance(arg, ast.JoinedStr):
            # f-string: inspect constant pieces only.
            for value in arg.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    found.append((arg.lineno, value.value))
    for keyword in node.keywords:
        if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
            found.append((keyword.value.lineno, keyword.value.value))
    return found


def _is_path_or_open_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name) and func.id in {"open", "Path"}:
        return True
    if isinstance(func, ast.Attribute) and func.attr in {"open", "Path"}:
        return True
    return False


def scan_python_ast(relative_path: str, source: str) -> list[Violation]:
    """Detect open()/Path() string arguments that escape the tree."""

    violations: list[Violation] = []
    try:
        tree = ast.parse(source, filename=relative_path)
    except SyntaxError:
        return violations

    escape_re = re.compile(r"(?:\.\./){2,}|(?:\.\.\\){2,}")
    abs_win = re.compile(r"(?i)\b[A-Za-z]:[\\/]")
    abs_unix = re.compile(r"(?i)/(?:home|Users)/")

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_path_or_open_call(node):
            continue
        for lineno, literal in _string_literals_from_call(node):
            if escape_re.search(literal):
                violations.append(
                    Violation(
                        "path_call_parent_escape",
                        relative_path,
                        lineno,
                        detail=literal[:80],
                    )
                )
            if abs_win.search(literal) or abs_unix.search(literal):
                violations.append(
                    Violation(
                        "path_call_absolute_literal",
                        relative_path,
                        lineno,
                        detail=literal[:80],
                    )
                )
    return violations


def _iter_scan_files(root: Path) -> Iterable[tuple[Path, str, bytes]]:
    for name in SCAN_ROOTS:
        base = root / name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            relative = path.relative_to(root)
            if any(part in SKIPPED_DIRECTORIES for part in relative.parts):
                continue
            if not path.is_file():
                continue
            yield path, relative.as_posix(), path.read_bytes()


def scan_tree(root: Path) -> tuple[list[Violation], int]:
    """Scan ``src/`` and ``tests/`` under *root* for isolation violations."""

    rules = content_rules()
    violations: list[Violation] = []
    scanned_files = 0

    for path, relative_text, payload in _iter_scan_files(root):
        scanned_files += 1
        for rule in rules:
            if rule.pattern.search(relative_text):
                violations.append(Violation(rule.rule_id, relative_text, None))

        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            continue

        for rule in rules:
            for match in rule.pattern.finditer(text):
                line_number = text.count("\n", 0, match.start()) + 1
                violations.append(
                    Violation(rule.rule_id, relative_text, line_number, detail=match.group(0)[:80])
                )

        if path.suffix == ".py":
            violations.extend(scan_python_ast(relative_text, text))

    return violations, scanned_files


def scan_text_blob(relative_path: str, text: str) -> list[Violation]:
    """Scan a single in-memory text blob (used by regression tests)."""

    violations: list[Violation] = []
    for rule in content_rules():
        for match in rule.pattern.finditer(text):
            line_number = text.count("\n", 0, match.start()) + 1
            violations.append(
                Violation(rule.rule_id, relative_path, line_number, detail=match.group(0)[:80])
            )
    if relative_path.endswith(".py"):
        violations.extend(scan_python_ast(relative_path, text))
    return violations


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    violations, scanned_files = scan_tree(root)
    if violations:
        for violation in violations:
            location = violation.relative_path
            if violation.line_number is not None:
                location += f":{violation.line_number}"
            detail = f" {violation.detail!r}" if violation.detail else ""
            print(f"FAIL {violation.rule_id} {location}{detail}")
        print(
            f"repo_isolation=FAIL scanned_files={scanned_files} violations={len(violations)}"
        )
        return 1

    print(f"repo_isolation=PASS scanned_files={scanned_files} violations=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
