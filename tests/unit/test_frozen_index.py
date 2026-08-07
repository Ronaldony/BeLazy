"""Unit tests for generic frozen-index write-path denial (synthetic fixtures only)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_factory.storage import (
    FrozenIndexEntry,
    FrozenIndexViolation,
    WorkspaceInitPlanStatus,
    assert_write_allowed,
    frozen_index_from_entries,
    load_frozen_index,
    normalize_frozen_path,
    plan_materialize,
)


def _index(*paths: str) -> object:
    return frozen_index_from_entries(
        FrozenIndexEntry(path=path, sha256="abc", status="historical") for path in paths
    )


def test_assert_write_allowed_rejects_indexed_path() -> None:
    index = _index("records/locked.txt")
    with pytest.raises(FrozenIndexViolation, match="frozen by index"):
        assert_write_allowed("records/locked.txt", index)


def test_assert_write_allowed_permits_non_indexed_path() -> None:
    index = _index("records/locked.txt")
    assert_write_allowed("records/free.txt", index)
    assert_write_allowed("other/dir/file.md", index)


def test_path_normalization_is_consistent_for_separators_and_case() -> None:
    index = _index("Records/Locked.txt")
    for candidate in (
        "Records/Locked.txt",
        r"Records\Locked.txt",
        "records/locked.txt",
        "./Records/Locked.txt",
        r".\Records\Locked.txt",
        "Records//Locked.txt",
    ):
        with pytest.raises(FrozenIndexViolation):
            assert_write_allowed(candidate, index)
        assert normalize_frozen_path(candidate) == normalize_frozen_path("Records/Locked.txt")

    assert_write_allowed(r"Records\other.txt", index)


def test_empty_index_blocks_nothing() -> None:
    empty = frozen_index_from_entries([])
    assert_write_allowed("anything/at/all.txt", empty)
    assert_write_allowed("records/locked.txt", None)


def test_plan_materialize_refuses_when_destination_hits_frozen_index(tmp_path: Path) -> None:
    template = tmp_path / "template"
    template.mkdir()
    (template / "notes.txt").write_text("safe payload\n", encoding="utf-8")
    (template / "nested").mkdir()
    (template / "nested" / "locked.txt").write_text("must not land\n", encoding="utf-8")

    index = _index("nested/locked.txt")
    target = tmp_path / "workspace_out"

    plan = plan_materialize(template, target, frozen_index=index)

    assert plan.status is WorkspaceInitPlanStatus.REJECTED_FROZEN_INDEX
    assert plan.executed is False
    assert plan.operations == ()
    assert "frozen by index" in (plan.rejection_reason or "")
    assert not target.exists()


def test_plan_materialize_without_frozen_index_still_ready(tmp_path: Path) -> None:
    """Default frozen_index=None skips the guard and returns a READY plan."""

    template = tmp_path / "template"
    template.mkdir()
    (template / "notes.txt").write_text("ok\n", encoding="utf-8")
    target = tmp_path / "out"
    plan = plan_materialize(template, target)
    assert plan.status is WorkspaceInitPlanStatus.READY
    assert plan.executed is False
    assert len(plan.operations) == 1
    assert plan.operations[0].destination_relative == "notes.txt"
    assert not target.exists()


def test_load_frozen_index_json_roundtrip(tmp_path: Path) -> None:
    document = {
        "artifact_version": "frozen-history-index/1.0",
        "entries": [
            {"path": "a/b.txt", "sha256": "00" * 32, "status": "historical"},
            {"path": r"c\d.txt", "sha256": "11" * 32, "status": "active"},
        ],
    }
    path = tmp_path / "index.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    loaded = load_frozen_index(path)
    assert len(loaded) == 2
    assert loaded.contains("a/b.txt")
    assert loaded.contains("c/d.txt")
    with pytest.raises(FrozenIndexViolation):
        assert_write_allowed(r"c\d.txt", loaded)
