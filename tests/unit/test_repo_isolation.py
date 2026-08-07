"""Regression tests for tools/check_repo_isolation.py.

Violation strings are assembled at runtime so this file itself does not
introduce isolation failures when the real ``src/`` and ``tests/`` trees are
scanned.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
from types import ModuleType


def _load_repo_isolation() -> ModuleType:
    repository_root = Path(__file__).resolve().parents[2]
    module_path = repository_root / "tools" / "check_repo_isolation.py"
    module_name = "check_repo_isolation"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec so dataclass evaluation under
    # ``from __future__ import annotations`` can resolve the module namespace.
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_repository_isolation_gate_passes() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    checker = repository_root / "tools" / "check_repo_isolation.py"
    completed = subprocess.run(
        [sys.executable, str(checker)],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "repo_isolation=PASS" in completed.stdout


def test_isolation_guard_detects_injected_violations() -> None:
    """Prove the guard is not a no-op: synthetic blobs must fail closed."""

    isolation = _load_repo_isolation()
    sibling = "Boss" + "Kimu"
    windows_abs = "C:" + "\\" + "Users" + "\\" + "example"
    unix_abs = "/" + "home" + "/" + "example"
    parent_escape = ".." + "/" + ".." + "/" + "outside.json"

    blobs = {
        "probe_sibling.py": f'label = "{sibling}"\n',
        "probe_win.py": f'path = r"{windows_abs}"\n',
        "probe_unix.py": f'path = "{unix_abs}"\n',
        "probe_escape.py": f'rel = "{parent_escape}"\n',
        "probe_path_call.py": f'from pathlib import Path\np = Path("{parent_escape}")\n',
        "probe_open_call.py": f'open("{windows_abs}")\n',
    }

    expected_rule_hits: set[str] = set()
    for name, text in blobs.items():
        hits = isolation.scan_text_blob(name, text)
        assert hits, f"expected violations in {name}, got none"
        expected_rule_hits.update(item.rule_id for item in hits)

    assert "sibling_workspace_identity" in expected_rule_hits
    assert (
        "windows_absolute_path" in expected_rule_hits
        or "path_call_absolute_literal" in expected_rule_hits
    )
    assert "unix_home_absolute_path" in expected_rule_hits
    assert (
        "parent_directory_escape" in expected_rule_hits
        or "path_call_parent_escape" in expected_rule_hits
    )
    assert (
        "path_call_parent_escape" in expected_rule_hits
        or "path_call_absolute_literal" in expected_rule_hits
    )


def test_isolation_scan_tree_on_temp_workspace(tmp_path: Path) -> None:
    """Full-tree scan finds a planted violation under a fake src/ layout."""

    isolation = _load_repo_isolation()
    src = tmp_path / "src" / "pkg"
    src.mkdir(parents=True)
    planted = src / "leaky.py"
    planted.write_text(
        'MARKER = "' + "Boss" + "Kimu" + '"\n',
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    violations, scanned = isolation.scan_tree(tmp_path)
    assert scanned >= 1
    assert any(item.rule_id == "sibling_workspace_identity" for item in violations)
