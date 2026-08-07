"""Regression tests for tools/check_side_effect_free.py.

Synthetic violation strings are assembled at runtime so this file itself does
not introduce side-effect tokens that the real ``src/`` scan would need to
exclude.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
from types import ModuleType


def _load_side_effect_free() -> ModuleType:
    repository_root = Path(__file__).resolve().parents[2]
    module_path = repository_root / "tools" / "check_side_effect_free.py"
    module_name = "check_side_effect_free"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_side_effect_free_gate_passes() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    checker = repository_root / "tools" / "check_side_effect_free.py"
    completed = subprocess.run(
        [sys.executable, str(checker)],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "side_effect_free=PASS" in completed.stdout


def test_side_effect_guard_detects_injected_violations() -> None:
    """Prove the guard is not a no-op: synthetic blobs must fail closed."""

    gate = _load_side_effect_free()
    # Assemble banned fragments so this test source is not self-flagging for purity.
    copy_name = "shutil" + "." + "copy"
    rmtree_name = "shutil" + "." + "rmtree"
    mkdir_call = "." + "mkdir" + "("
    zip_write = "ZipFile" + "(" + "path, " + "'w'" + ")"
    sub_mod = "sub" + "process"
    open_write = "open" + "(" + "path, " + "'w'" + ")"
    os_replace = "os" + "." + "replace"
    path_replace = "Path('a')" + "." + "replace"
    path_open_update = "Path('a')" + "." + "open" + "('r+b')"
    path_symlink = "Path('a')" + "." + "symlink_to"
    handle_truncate = "handle" + "." + "truncate"

    blobs = {
        "probe_copy.py": f"import shutil\n{copy_name}('a', 'b')\n",
        "probe_rmtree.py": f"import shutil\n{rmtree_name}('x')\n",
        "probe_mkdir.py": f"from pathlib import Path\nPath('d'){mkdir_call})\n",
        "probe_zip.py": f"from zipfile import ZipFile\n{zip_write}\n",
        "probe_sub.py": f"import {sub_mod}\n{sub_mod}.run(['true'])\n",
        "probe_open_w.py": f"{open_write}\n",
        "probe_write_text.py": "Path('f').write_text('x')\n",
        "probe_os_replace.py": f"import os\n{os_replace}('a', 'b')\n",
        "probe_path_replace.py": f"from pathlib import Path\n{path_replace}('b')\n",
        "probe_path_open_update.py": (
            f"from pathlib import Path\nwith {path_open_update} as handle:\n"
            "    handle.write(b'x')\n"
        ),
        "probe_path_symlink.py": (
            f"from pathlib import Path\n{path_symlink}('b')\n"
        ),
        "probe_handle_truncate.py": f"{handle_truncate}(0)\n",
    }

    expected_rule_hits: set[str] = set()
    for name, text in blobs.items():
        hits = gate.scan_text_blob(name, text)
        assert hits, f"expected violations in {name}, got none: {text!r}"
        expected_rule_hits.update(item.rule_id for item in hits)

    assert any("copy" in rule or "shutil_copy" in rule for rule in expected_rule_hits)
    assert any("rmtree" in rule for rule in expected_rule_hits)
    assert any("mkdir" in rule for rule in expected_rule_hits)
    assert any("zip" in rule.lower() or "Zip" in rule for rule in expected_rule_hits)
    assert any("sub" in rule for rule in expected_rule_hits)
    assert any("open" in rule or "write" in rule for rule in expected_rule_hits)
    assert any("replace" in rule for rule in expected_rule_hits)
    assert any("symlink" in rule for rule in expected_rule_hits)
    assert any("truncate" in rule for rule in expected_rule_hits)


def test_side_effect_scan_tree_on_temp_workspace(tmp_path: Path) -> None:
    """Full-tree scan finds a planted write under a fake src/ layout."""

    gate = _load_side_effect_free()
    src = tmp_path / "src" / "pkg"
    src.mkdir(parents=True)
    planted = src / "leaky.py"
    # Build "shutil.copy2" without a continuous banned token in this test file's
    # source that would confuse manual review (runtime assembly is fine).
    planted.write_text(
        "import shutil\nshutil." + "copy2" + "('a', 'b')\n",
        encoding="utf-8",
    )
    violations, scanned = gate.scan_tree(tmp_path)
    assert scanned >= 1
    assert any("copy" in item.rule_id for item in violations)
