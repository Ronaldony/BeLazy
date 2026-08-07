from __future__ import annotations

import json
from pathlib import Path


def test_fixture_is_synthetic_and_repository_local() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    fixture = repository_root / "tests" / "fixtures" / "sample_artifact.json"
    fixture.resolve().relative_to(repository_root)
    data = json.loads(fixture.read_text(encoding="utf-8"))
    assert data["scope_id"] == "scope-a"
    assert data["adapter_id"] == "adapter-a"

