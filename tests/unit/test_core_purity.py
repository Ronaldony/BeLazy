from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def test_repository_purity_gate_passes() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    checker = repository_root / "tools" / "check_core_purity.py"
    completed = subprocess.run(
        [sys.executable, str(checker)],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

