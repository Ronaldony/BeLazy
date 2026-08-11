"""Fail when first-party package dependencies contain a non-trivial SCC."""

from __future__ import annotations

import ast
from pathlib import Path
import sys


def _package_for_file(package_root: Path, path: Path) -> tuple[str, ...]:
    relative = path.relative_to(package_root)
    if path.name == "__init__.py":
        return (package_root.name, *relative.parts[:-1])
    return (package_root.name, *relative.with_suffix("").parts[:-1])


def _import_name(node: ast.ImportFrom, current: tuple[str, ...]) -> str:
    if node.level == 0:
        return node.module or ""
    retained = len(current) - (node.level - 1)
    if retained < 1:
        return ""
    prefix = current[:retained]
    if node.module:
        return ".".join((*prefix, node.module))
    return ".".join(prefix)


def _import_names(
    node: ast.Import | ast.ImportFrom,
    current: tuple[str, ...],
    package_name: str,
) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    base = _import_name(node, current)
    names = [base]
    if base == package_name:
        names.extend(f"{base}.{alias.name}" for alias in node.names)
    return tuple(names)


def package_dependency_graph(package_root: Path) -> dict[str, tuple[str, ...]]:
    """Return first-level package edges; dependency-root modules are leaves."""

    package_names = {
        path.name
        for path in package_root.iterdir()
        if path.is_dir() and (path / "__init__.py").is_file()
    }
    graph: dict[str, set[str]] = {name: set() for name in package_names}
    for path in sorted(package_root.rglob("*.py")):
        relative = path.relative_to(package_root)
        if len(relative.parts) < 2 or relative.parts[0] not in package_names:
            continue
        source = relative.parts[0]
        current = _package_for_file(package_root, path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            names = _import_names(node, current, package_root.name)
            for name in names:
                parts = name.split(".")
                if len(parts) < 2 or parts[0] != package_root.name:
                    continue
                target = parts[1]
                if target in package_names and target != source:
                    graph[source].add(target)
    return {name: tuple(sorted(targets)) for name, targets in sorted(graph.items())}


def dependency_root_imports(package_root: Path) -> tuple[tuple[str, str], ...]:
    """Return forbidden root-module -> first-party package dependencies."""

    package_names = {
        path.name
        for path in package_root.iterdir()
        if path.is_dir() and (path / "__init__.py").is_file()
    }
    violations: set[tuple[str, str]] = set()
    for path in sorted(package_root.glob("*.py")):
        if path.name == "__init__.py":
            continue
        current = (package_root.name,)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for name in _import_names(node, current, package_root.name):
                parts = name.split(".")
                if (
                    len(parts) >= 2
                    and parts[0] == package_root.name
                    and parts[1] in package_names
                ):
                    violations.add((path.stem, parts[1]))
    return tuple(sorted(violations))


def nontrivial_sccs(graph: dict[str, tuple[str, ...]]) -> tuple[tuple[str, ...], ...]:
    """Return deterministic Tarjan strongly connected components of size > 1."""

    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[tuple[str, ...]] = []
    next_index = 0

    def visit(node: str) -> None:
        nonlocal next_index
        indices[node] = next_index
        lowlinks[node] = next_index
        next_index += 1
        stack.append(node)
        on_stack.add(node)
        for target in graph.get(node, ()):
            if target not in indices:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[target])
        if lowlinks[node] != indices[node]:
            return
        component: list[str] = []
        while True:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == node:
                break
        if len(component) > 1:
            components.append(tuple(sorted(component)))

    for node in sorted(graph):
        if node not in indices:
            visit(node)
    return tuple(sorted(components))


def validate_package_dependencies(package_root: Path) -> list[str]:
    graph = package_dependency_graph(package_root)
    errors = [
        "package_scc:" + ",".join(component)
        for component in nontrivial_sccs(graph)
    ]
    errors.extend(
        f"dependency_root_import:{source}->{target}"
        for source, target in dependency_root_imports(package_root)
    )
    return sorted(errors)


def main() -> int:
    root = Path(__file__).resolve().parents[1] / "src" / "video_factory"
    graph = package_dependency_graph(root)
    errors = validate_package_dependencies(root)
    if errors:
        for error in errors:
            print(f"FAIL {error}")
        print(
            "package_dependencies=FAIL "
            f"packages={len(graph)} edges={sum(map(len, graph.values()))} "
            f"scc={len(errors)}"
        )
        return 1
    print(
        "package_dependencies=PASS "
        f"packages={len(graph)} edges={sum(map(len, graph.values()))} scc=0"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
