from __future__ import annotations

import importlib
import os
from pathlib import Path
import subprocess
import sys


def test_every_package_module_imports() -> None:
    source_root = Path(__file__).resolve().parents[2] / "src"
    package_root = source_root / "video_factory"
    modules = []
    for path in package_root.rglob("*.py"):
        relative = path.relative_to(source_root).with_suffix("")
        parts = relative.parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        modules.append(".".join(parts))

    for module_name in sorted(set(modules)):
        importlib.import_module(module_name)


def test_policy_first_import_order_has_no_engine_cycle() -> None:
    source_root = Path(__file__).resolve().parents[2] / "src"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(source_root)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import video_factory.policy; "
                "import video_factory.config; "
                "from video_factory.engine import plan_next_step, ArtifactSnapshot"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

