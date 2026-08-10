"""Verify one-way dependency and offline safety of the W06 runtime package."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import sys


CORE_ROOT = Path("src/video_factory")
RUNTIME_ROOT = Path("src/video_factory_runtime")
FORBIDDEN_RUNTIME_IMPORTS = frozenset(
    {
        "ftplib",
        "httpx",
        "paramiko",
        "requests",
        "socket",
        "subprocess",
        "urllib",
    }
)


@dataclass(frozen=True, slots=True)
class RuntimeBoundaryViolation:
    reason_code: str
    path: str
    line: int
    detail: str


def _python_files(root: Path, relative: Path) -> tuple[Path, ...]:
    target = root / relative
    if not target.is_dir():
        return ()
    return tuple(sorted(target.rglob("*.py")))


def scan_runtime_boundary(
    root: Path,
) -> tuple[tuple[RuntimeBoundaryViolation, ...], int, int]:
    violations: list[RuntimeBoundaryViolation] = []
    core_files = _python_files(root, CORE_ROOT)
    runtime_files = _python_files(root, RUNTIME_ROOT)
    if not runtime_files:
        violations.append(
            RuntimeBoundaryViolation(
                "runtime.package.missing", RUNTIME_ROOT.as_posix(), 0, "missing"
            )
        )
    for path in core_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as error:
            violations.append(
                RuntimeBoundaryViolation(
                    "runtime.core.syntax",
                    path.relative_to(root).as_posix(),
                    error.lineno or 0,
                    "syntax error",
                )
            )
            continue
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = (node.module,)
            for module in modules:
                if module == "video_factory_runtime" or module.startswith(
                    "video_factory_runtime."
                ):
                    violations.append(
                        RuntimeBoundaryViolation(
                            "runtime.dependency.reverse_import",
                            path.relative_to(root).as_posix(),
                            getattr(node, "lineno", 0),
                            module,
                        )
                    )
    for path in runtime_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as error:
            violations.append(
                RuntimeBoundaryViolation(
                    "runtime.package.syntax",
                    path.relative_to(root).as_posix(),
                    error.lineno or 0,
                    "syntax error",
                )
            )
            continue
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = (node.module,)
            for module in modules:
                if module.split(".", 1)[0] in FORBIDDEN_RUNTIME_IMPORTS:
                    violations.append(
                        RuntimeBoundaryViolation(
                            "runtime.network_or_process.import",
                            path.relative_to(root).as_posix(),
                            getattr(node, "lineno", 0),
                            module,
                        )
                    )
        for node in tree.body:
            if isinstance(node, ast.Expr):
                candidate = node.value
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                candidate = node.value
            else:
                continue
            if not isinstance(candidate, ast.Call):
                continue
            name = ""
            if isinstance(candidate.func, ast.Name):
                name = candidate.func.id
            elif isinstance(candidate.func, ast.Attribute):
                name = candidate.func.attr
            if name in {
                "connect",
                "mkdir",
                "open",
                "unlink",
                "write_bytes",
                "write_text",
            }:
                violations.append(
                    RuntimeBoundaryViolation(
                        "runtime.import_side_effect",
                        path.relative_to(root).as_posix(),
                        getattr(node, "lineno", 0),
                        name,
                    )
                )
    ordered = tuple(
        sorted(
            violations,
            key=lambda item: (item.path, item.line, item.reason_code, item.detail),
        )
    )
    return ordered, len(core_files), len(runtime_files)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    violations, core_files, runtime_files = scan_runtime_boundary(root)
    if violations:
        for item in violations:
            print(f"FAIL {item.reason_code} {item.path}:{item.line} {item.detail}")
        print(
            "runtime_boundary=FAIL "
            f"core_files={core_files} runtime_files={runtime_files} "
            f"violations={len(violations)}"
        )
        return 1
    print(
        "runtime_boundary=PASS "
        f"core_files={core_files} runtime_files={runtime_files} violations=0"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
