"""Unit tests for the channel-agnostic workspace init planner (plan-only)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest
import video_factory.storage.workspace_init as workspace_init_module

from video_factory.cli import handle_init
from video_factory.policy import resolve_workflow_policy
from video_factory.storage import (
    WorkspaceInitPlanStatus,
    plan_materialize,
)
from video_factory.storage.workspace_init import WorkspaceInitEngine
from video_factory.storage import tree_safety


def _write_channel_config(path: Path, *, channel_id: str = "synth-channel") -> None:
    document = {
        "artifact_version": "channel-config/1.0",
        "config_contract": "1.0",
        "channel_id": channel_id,
        "settings": {
            "media": {
                "duration_seconds": 45,
                "aspect_ratio": {"width": 16, "height": 9},
                "platforms": ["platform-example"],
            },
            "identity": {"recurring_character_ids": []},
        },
        "extensions": {},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _write_concept_config(
    path: Path,
    *,
    concept_id: str,
    character_ids: list[str],
) -> None:
    document = {
        "artifact_version": "concept-config/1.0",
        "config_contract": "1.0",
        "concept_id": concept_id,
        "settings": {
            "identity": {"recurring_character_ids": character_ids},
        },
        "extensions": {},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _build_valid_template(root: Path) -> Path:
    template = root / "template_a"
    _write_channel_config(template / "channel.json")
    _write_concept_config(
        template / "concepts" / "with-character.json",
        concept_id="concept-with-character",
        character_ids=["SYNTH_MASCOT"],
    )
    _write_concept_config(
        template / "concepts" / "no-character.json",
        concept_id="concept-no-character",
        character_ids=[],
    )
    bindings = {
        "artifact_kind": "channel-provider-bindings",
        "bindings": [
            {
                "capability_id": "media.video.generate",
                "adapter_ids": ["adapter-example-provider"],
                "maximum_execution_mode": "human_only",
            }
        ],
    }
    (template / "provider_bindings.json").write_text(
        json.dumps(bindings, indent=2) + "\n",
        encoding="utf-8",
    )
    (template / "README.md").write_text("synthetic template fixture\n", encoding="utf-8")
    return template


def _snapshot_tree(root: Path) -> dict[str, str | None]:
    """Map relative paths under *root* to sha256 (files) or None (dirs)."""

    snapshot: dict[str, str | None] = {}
    if not root.exists():
        return snapshot
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_dir():
            snapshot[rel + "/"] = None
        elif path.is_file():
            snapshot[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def test_plan_materialize_ready_lists_operations(tmp_path: Path) -> None:
    template = _build_valid_template(tmp_path)
    target = tmp_path / "workspace_out"
    before = _snapshot_tree(tmp_path)

    plan = plan_materialize(template, target)

    assert plan.status is WorkspaceInitPlanStatus.READY
    assert plan.executed is False
    assert plan.planning_only is True
    assert plan.authorization_ready is False
    assert plan.configs_validated == 3
    assert len(plan.operations) >= 4
    names = {item.destination_relative for item in plan.operations}
    assert "channel.json" in names
    assert "concepts/with-character.json" in names
    assert "provider_bindings.json" in names
    assert "README.md" in names
    for item in plan.operations:
        source = template / item.source_relative
        expected = hashlib.sha256(source.read_bytes()).hexdigest()
        assert item.source_sha256 == expected
        assert item.source_relative == item.destination_relative

    after = _snapshot_tree(tmp_path)
    assert after == before
    assert not target.exists()


def test_plan_materialize_refuses_nonempty_target(tmp_path: Path) -> None:
    template = _build_valid_template(tmp_path)
    target = tmp_path / "existing"
    target.mkdir()
    (target / "keep-me.txt").write_text("pre-existing\n", encoding="utf-8")
    before = _snapshot_tree(tmp_path)

    plan = plan_materialize(template, target)

    assert plan.status is WorkspaceInitPlanStatus.REJECTED_TARGET_NONEMPTY
    assert plan.executed is False
    assert plan.operations == ()
    assert "not empty" in (plan.rejection_reason or "")
    assert (target / "keep-me.txt").read_text(encoding="utf-8") == "pre-existing\n"
    assert not (target / "channel.json").exists()
    assert _snapshot_tree(tmp_path) == before


def test_plan_materialize_schema_violation_has_no_operations(tmp_path: Path) -> None:
    template = _build_valid_template(tmp_path)
    bad = {
        "artifact_version": "channel-config/1.0",
        "config_contract": "1.0",
        "settings": {},
    }
    (template / "broken-channel.json").write_text(
        json.dumps(bad, indent=2) + "\n",
        encoding="utf-8",
    )
    target = tmp_path / "should_not_exist"
    before = _snapshot_tree(tmp_path)

    plan = plan_materialize(template, target)

    assert plan.status is WorkspaceInitPlanStatus.REJECTED_SCHEMA
    assert plan.operations == ()
    assert plan.executed is False
    assert "validation failed" in (plan.rejection_reason or "")
    assert not target.exists()
    assert _snapshot_tree(tmp_path) == before


def test_engine_does_not_touch_workflow_mode(tmp_path: Path) -> None:
    """Init planning never calls workflow policy resolution."""

    template = _build_valid_template(tmp_path)
    target = tmp_path / "mode_independent"
    engine = WorkspaceInitEngine()
    plan = engine.plan_materialize(template, target)
    assert plan.status is WorkspaceInitPlanStatus.READY
    assert plan.executed is False
    assert not target.exists()

    with pytest.raises(Exception, match="does not choose a production default"):
        resolve_workflow_policy(None)

    target2 = tmp_path / "via_handler"
    handled = handle_init(template_dir=template, target_dir=target2)
    assert handled.exit_code == 0
    assert handled.status == "ok"
    assert handled.payload is not None
    assert handled.payload["executed"] is False
    assert handled.payload["planning_only"] is True
    assert handled.payload["authorization_ready"] is False
    assert not target2.exists()


def test_handle_init_reports_rejection_without_writes(tmp_path: Path) -> None:
    missing = tmp_path / "no_such_template"
    target = tmp_path / "handler_fail_target"
    before = _snapshot_tree(tmp_path)
    result = handle_init(template_dir=missing, target_dir=target)
    assert result.exit_code == 1
    assert result.status == "init_rejected"
    assert not target.exists()
    assert _snapshot_tree(tmp_path) == before


def test_plan_materialize_rejects_symlinked_template_leaf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template = _build_valid_template(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    link = template / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        def reject_tree(_root: Path):
            raise tree_safety.SafeTreeError(
                "tree.link_or_reparse", link, "synthetic unavailable-link probe"
            )

        monkeypatch.setattr(
            workspace_init_module, "iter_regular_files_no_follow", reject_tree
        )

    plan = plan_materialize(template, tmp_path / "target")

    assert plan.status is WorkspaceInitPlanStatus.REJECTED_UNSAFE_TREE
    assert plan.operations == ()
    assert plan.authorization_ready is False
    assert "link_or_reparse" in (plan.rejection_reason or "")
    assert not (tmp_path / "target").exists()


def test_plan_materialize_rejects_symlinked_template_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = _build_valid_template(tmp_path)
    linked = tmp_path / "linked-template"
    try:
        linked.symlink_to(real, target_is_directory=True)
    except OSError:
        def reject_tree(_root: Path):
            raise tree_safety.SafeTreeError(
                "tree.link_or_reparse", linked, "synthetic unavailable-link probe"
            )

        monkeypatch.setattr(
            workspace_init_module, "iter_regular_files_no_follow", reject_tree
        )

    plan = plan_materialize(linked, tmp_path / "target")

    assert plan.status is WorkspaceInitPlanStatus.REJECTED_UNSAFE_TREE
    assert plan.operations == ()
    assert not (tmp_path / "target").exists()


def test_tree_safety_recognizes_windows_reparse_attribute() -> None:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    assert tree_safety._is_reparse_point(  # noqa: SLF001 - security regression
        SimpleNamespace(st_file_attributes=reparse_flag)
    )
    assert not tree_safety._is_reparse_point(  # noqa: SLF001
        SimpleNamespace(st_file_attributes=0)
    )
