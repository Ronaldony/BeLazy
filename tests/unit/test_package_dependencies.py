"""Architecture gate for a cycle-free first-party package graph."""

from __future__ import annotations

from pathlib import Path

from tools.check_package_dependencies import (
    dependency_root_imports,
    nontrivial_sccs,
    package_dependency_graph,
    validate_package_dependencies,
)


def test_target_package_dependency_graph_has_no_nontrivial_scc() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "video_factory"
    graph = package_dependency_graph(root)
    assert nontrivial_sccs(graph) == ()
    assert dependency_root_imports(root) == ()
    assert validate_package_dependencies(root) == []


def test_package_dependency_gate_rejects_a_cycle(tmp_path: Path) -> None:
    root = tmp_path / "sample"
    left = root / "left"
    right = root / "right"
    left.mkdir(parents=True)
    right.mkdir()
    (left / "__init__.py").write_text("from sample import right\n", encoding="utf-8")
    (right / "__init__.py").write_text("from sample.left import value\n", encoding="utf-8")
    graph = package_dependency_graph(root)
    assert nontrivial_sccs(graph) == (("left", "right"),)
    assert validate_package_dependencies(root) == ["package_scc:left,right"]


def test_dependency_root_module_must_not_import_a_first_party_package(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sample"
    left = root / "left"
    left.mkdir(parents=True)
    (left / "__init__.py").write_text("value = 1\n", encoding="utf-8")
    (root / "kernel.py").write_text(
        "from sample.left import value\n",
        encoding="utf-8",
    )
    assert dependency_root_imports(root) == (("kernel", "left"),)
    assert validate_package_dependencies(root) == [
        "dependency_root_import:kernel->left"
    ]
