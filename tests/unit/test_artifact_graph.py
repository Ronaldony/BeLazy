"""Validated artifact graph and exact-byte snapshot tests."""

from __future__ import annotations

import hashlib
import json

import pytest

from video_factory.engine import (
    ArtifactGraphError,
    build_artifact_graph,
    make_artifact_snapshot,
    make_artifact_snapshot_from_json_bytes,
)


def _brief(episode_id: str = "ep-1") -> dict[str, object]:
    return {
        "artifact_version": "brief/1.0",
        "rules_version": "rules-test",
        "episode_id": episode_id,
        "summary": "summary",
        "hook": "hook",
        "development": "development",
        "ending": "ending",
        "risks": [],
    }


def test_exact_json_bytes_are_hash_bound() -> None:
    data = json.dumps(_brief(), indent=2).encode("utf-8")
    snapshot = make_artifact_snapshot_from_json_bytes(
        "01_brief/brief.json",
        data,
        expected_sha256=hashlib.sha256(data).hexdigest(),
    )
    assert str(snapshot.sha256) == hashlib.sha256(data).hexdigest()
    assert build_artifact_graph([snapshot]).ok is True

    with pytest.raises(ArtifactGraphError, match="do not match"):
        make_artifact_snapshot_from_json_bytes(
            "01_brief/brief.json",
            data,
            expected_sha256="0" * 64,
        )


def test_mixed_episode_ids_and_ambiguous_current_artifacts_fail_closed() -> None:
    graph = build_artifact_graph(
        [
            make_artifact_snapshot("01_brief/a.json", _brief("ep-a")),
            make_artifact_snapshot("01_brief/b.json", _brief("ep-b")),
        ]
    )
    codes = {item.code for item in graph.findings}
    assert "mixed_episode_ids" in codes
    assert "ambiguous_current_artifact" in codes


def test_invalid_schema_is_a_graph_finding() -> None:
    invalid = make_artifact_snapshot(
        "01_brief/invalid.json",
        {
            "artifact_version": "brief/1.0",
            "rules_version": "rules-test",
            "episode_id": "ep-1",
        },
    )
    graph = build_artifact_graph([invalid])
    assert graph.ok is False
    assert graph.findings[0].code == "invalid_artifact"
