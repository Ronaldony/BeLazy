"""Fail when core ``src/`` performs writes or launches external processes.

Complements purity (channel identity) and isolation (tree escape). This gate
enforces the plan-only invariant: core may observe, validate, compute, and
build structured Plan objects — it must not write files or start processes.

Detection is AST-first with a regex safety net. The scanner itself lives under
``tools/`` and is outside the scan root.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import re
import sys
from typing import Iterable


SKIPPED_DIRECTORIES = frozenset(
    {".git", ".pytest_cache", ".mypy_cache", ".ruff_cache", "__pycache__", ".venv"}
)

SCAN_ROOT = "src"

# Regex safety net applied to source text (and used by regression tests).
# Patterns intentionally match the acceptance-criteria search set.
TEXT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("subprocess_import_or_use", re.compile(r"\bsubprocess\b")),
    ("os_system", re.compile(r"\bos\.system\b")),
    ("shutil_copy", re.compile(r"\bshutil\.copy(?:2|tree)?\b")),
    ("shutil_move", re.compile(r"\bshutil\.move\b")),
    ("shutil_rmtree", re.compile(r"\bshutil\.rmtree\b")),
    ("os_makedirs", re.compile(r"\bos\.makedirs\b")),
    ("os_remove", re.compile(r"\bos\.remove\b")),
    ("os_rename", re.compile(r"\bos\.rename\b")),
    ("os_unlink", re.compile(r"\bos\.unlink\b")),
    ("path_mkdir_call", re.compile(r"\.mkdir\s*\(")),
    ("zipfile_write_mode", re.compile(r"\bZipFile\s*\([^)]*['\"]w['\"]")),
    ("write_text", re.compile(r"\.write_text\s*\(")),
    ("write_bytes", re.compile(r"\.write_bytes\s*\(")),
    ("open_write_mode", re.compile(r"\bopen\s*\([^)]*mode\s*=\s*['\"][wa]")),
    ("open_write_positional", re.compile(r"\bopen\s*\([^,\n]+,\s*['\"][wa]")),
)


@dataclass(frozen=True, slots=True)
class Violation:
    rule_id: str
    relative_path: str
    line_number: int | None
    detail: str = ""


def _attr_chain(node: ast.AST) -> list[str]:
    parts: list[str] = []
    current: ast.AST | None = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    parts.reverse()
    return parts


def _call_name(node: ast.Call) -> str:
    chain = _attr_chain(node.func)
    if chain:
        return ".".join(chain)
    if isinstance(node.func, ast.Name):
        return node.func.id
    return ""


def _is_write_open_mode(mode: object) -> bool:
    if not isinstance(mode, str):
        return False
    # Any mode containing w, a, or x is a write/create mode.
    return any(flag in mode for flag in ("w", "a", "x"))


def scan_python_ast(relative_path: str, source: str) -> list[Violation]:
    """AST scan for write / process side effects."""

    violations: list[Violation] = []
    try:
        tree = ast.parse(source, filename=relative_path)
    except SyntaxError:
        return violations

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module_names: list[str] = []
            if isinstance(node, ast.Import):
                module_names = [alias.name.split(".")[0] for alias in node.names]
            elif node.module:
                module_names = [node.module.split(".")[0]]
            if "subprocess" in module_names:
                violations.append(
                    Violation(
                        "ast_import_subprocess",
                        relative_path,
                        getattr(node, "lineno", None),
                        detail="import subprocess",
                    )
                )
            continue

        if not isinstance(node, ast.Call):
            continue

        name = _call_name(node)
        lineno = getattr(node, "lineno", None)

        banned_calls = {
            "subprocess.run": "ast_subprocess_run",
            "subprocess.call": "ast_subprocess_call",
            "subprocess.Popen": "ast_subprocess_popen",
            "subprocess.check_call": "ast_subprocess_check_call",
            "subprocess.check_output": "ast_subprocess_check_output",
            "os.system": "ast_os_system",
            "os.makedirs": "ast_os_makedirs",
            "os.remove": "ast_os_remove",
            "os.rename": "ast_os_rename",
            "os.unlink": "ast_os_unlink",
            "shutil.copy": "ast_shutil_copy",
            "shutil.copy2": "ast_shutil_copy2",
            "shutil.copytree": "ast_shutil_copytree",
            "shutil.move": "ast_shutil_move",
            "shutil.rmtree": "ast_shutil_rmtree",
            "zipfile.ZipFile": "ast_zipfile_ctor",
            "ZipFile": "ast_zipfile_ctor",
        }
        if name in banned_calls:
            # ZipFile is only a violation when opened for write; check mode if present.
            if name in {"zipfile.ZipFile", "ZipFile"}:
                mode_val: object = "r"
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    mode_val = node.args[1].value
                for keyword in node.keywords:
                    if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                        mode_val = keyword.value.value
                if not _is_write_open_mode(mode_val):
                    continue
            violations.append(
                Violation(banned_calls[name], relative_path, lineno, detail=name)
            )
            continue

        # Path.mkdir / self.mkdir / anything.mkdir
        if isinstance(node.func, ast.Attribute) and node.func.attr == "mkdir":
            violations.append(
                Violation("ast_mkdir", relative_path, lineno, detail="mkdir")
            )
            continue

        # Path write helpers only — do not treat str.replace / Mapping.replace as writes.
        if isinstance(node.func, ast.Attribute) and node.func.attr in {
            "write_text",
            "write_bytes",
            "unlink",
            "rmdir",
            "touch",
        }:
            violations.append(
                Violation(
                    f"ast_path_{node.func.attr}",
                    relative_path,
                    lineno,
                    detail=node.func.attr,
                )
            )
            continue

        # open(..., "w"/"a"/...) 
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            mode_val = "r"
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                mode_val = node.args[1].value
            for keyword in node.keywords:
                if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                    mode_val = keyword.value.value
            if _is_write_open_mode(mode_val):
                violations.append(
                    Violation(
                        "ast_open_write",
                        relative_path,
                        lineno,
                        detail=f"open mode={mode_val!r}",
                    )
                )

    return violations


def scan_text_blob(relative_path: str, text: str) -> list[Violation]:
    """Scan a single in-memory text blob (used by regression tests)."""

    violations: list[Violation] = []
    for rule_id, pattern in TEXT_PATTERNS:
        for match in pattern.finditer(text):
            line_number = text.count("\n", 0, match.start()) + 1
            violations.append(
                Violation(
                    rule_id,
                    relative_path,
                    line_number,
                    detail=match.group(0)[:80],
                )
            )
    if relative_path.endswith(".py"):
        violations.extend(scan_python_ast(relative_path, text))
    return violations


def _iter_src_files(root: Path) -> Iterable[tuple[Path, str, bytes]]:
    base = root / SCAN_ROOT
    if not base.is_dir():
        return
    for path in sorted(base.rglob("*.py")):
        relative = path.relative_to(root)
        if any(part in SKIPPED_DIRECTORIES for part in relative.parts):
            continue
        if not path.is_file():
            continue
        yield path, relative.as_posix(), path.read_bytes()


def scan_tree(root: Path) -> tuple[list[Violation], int]:
    """Scan ``src/`` under *root* for side-effect violations."""

    violations: list[Violation] = []
    scanned_files = 0
    for path, relative_text, payload in _iter_src_files(root):
        scanned_files += 1
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            continue
        # Prefer AST; regex is a second pass for comment/string hits that AST
        # would miss when the acceptance criteria require zero textual hits.
        ast_hits = scan_python_ast(relative_text, text)
        violations.extend(ast_hits)
        for rule_id, pattern in TEXT_PATTERNS:
            for match in pattern.finditer(text):
                line_number = text.count("\n", 0, match.start()) + 1
                # Skip pure docstring mentions of allowed observation helpers?
                # No — plan-only requires the banned tokens not appear in src.
                violations.append(
                    Violation(
                        rule_id,
                        relative_text,
                        line_number,
                        detail=match.group(0)[:80],
                    )
                )
    # Deduplicate identical (rule, path, line, detail)
    unique: dict[tuple[object, ...], Violation] = {}
    for item in violations:
        key = (item.rule_id, item.relative_path, item.line_number, item.detail)
        unique[key] = item
    return list(unique.values()), scanned_files


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    violations, scanned_files = scan_tree(root)
    if violations:
        for violation in sorted(
            violations,
            key=lambda item: (item.relative_path, item.line_number or 0, item.rule_id),
        ):
            location = violation.relative_path
            if violation.line_number is not None:
                location += f":{violation.line_number}"
            detail = f" {violation.detail!r}" if violation.detail else ""
            print(f"FAIL {violation.rule_id} {location}{detail}")
        print(
            f"side_effect_free=FAIL scanned_files={scanned_files} "
            f"violations={len(violations)}"
        )
        return 1

    print(f"side_effect_free=PASS scanned_files={scanned_files} violations=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
