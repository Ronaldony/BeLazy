"""End-to-end pipeline over a synthetic workspace confined to tmp_path.

Scenario order:
1. Write workspace/channel/concept/episode config JSON under tmp_path
2. Merge layers and compute effective-config SHA-256
3. Resolve workflow policy for an explicit mode
4. CLI validate against the temporary documents
5. CLI doctor (structure only — purity is repository-local)
6. CLI retry twice with the same idempotency key

Nothing outside tmp_path is written by this test body. A sealed sibling
directory under basetemp proves the outside tree is untouched.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from video_factory import CORE_CONTRACT_VERSION, __version__
from video_factory.cli import (
    RetryIdempotencyLedger,
    handle_doctor,
    handle_retry,
    handle_validate,
)
from video_factory.config import (
    CONFIG_CONTRACT_VERSION,
    ConfigLayer,
    DeterministicConfigMerger,
    EffectiveScope,
    EffectiveVersions,
    config_source_from_document_bytes,
)
from video_factory.domain import HashDigest, OpaqueId
from video_factory.policy import resolve_workflow_policy

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _synthetic_documents() -> dict[str, dict[str, object]]:
    return {
        "workspace": {
            "artifact_version": "workspace-config/1.0",
            "config_contract": "1.0",
            "workspace_id": "workspace-a",
            "settings": {
                "media": {
                    "duration_seconds": 35,
                    "aspect_ratio": {"width": 9, "height": 16},
                    "platforms": ["platform-a"],
                },
                "execution": {"mode": "human_only"},
            },
            "extensions": {},
        },
        "channel": {
            "artifact_version": "channel-config/1.0",
            "config_contract": "1.0",
            "channel_id": "channel-a",
            "settings": {
                "media": {
                    "duration_seconds": 45,
                    "platforms": ["platform-b"],
                },
                "identity": {"recurring_character_ids": []},
            },
            "extensions": {},
        },
        "concept": {
            "artifact_version": "concept-config/1.0",
            "config_contract": "1.0",
            "concept_id": "concept-a",
            "settings": {
                "media": {"duration_seconds": 55},
                "identity": {"recurring_character_ids": []},
            },
            "extensions": {},
        },
        "episode": {
            "artifact_version": "episode-config/1.0",
            "config_contract": "1.0",
            "episode_id": "episode-a",
            "settings": {
                "media": {
                    "duration_seconds": 75,
                    "aspect_ratio": {"width": 16, "height": 9},
                    "platforms": ["platform-c"],
                }
            },
            "extensions": {},
        },
    }


@pytest.mark.integration
def test_temp_workspace_pipeline_stays_inside_tmp_path(tmp_path: Path) -> None:
    # Sealed sibling under the same basetemp parent — must remain unchanged.
    sealed = tmp_path.parent / f"sealed_outside_{tmp_path.name}"
    sealed.mkdir(exist_ok=True)
    marker = sealed / "do_not_touch.txt"
    marker.write_text("sealed-v1", encoding="utf-8")
    sealed_fingerprint = (
        marker.read_text(encoding="utf-8"),
        marker.stat().st_mtime_ns,
        marker.stat().st_size,
    )

    config_dir = tmp_path / "workspace" / "config"
    config_dir.mkdir(parents=True)
    documents = _synthetic_documents()
    written_paths: dict[str, Path] = {}
    for name, document in documents.items():
        path = config_dir / f"{name}.json"
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        written_paths[name] = path

    layer_map = {
        "workspace": ConfigLayer.WORKSPACE,
        "channel": ConfigLayer.CHANNEL,
        "concept": ConfigLayer.CONCEPT,
        "episode": ConfigLayer.EPISODE,
    }
    sources = [
        config_source_from_document_bytes(
            layer=layer_map[name],
            path=f"config/{name}.json",
            payload=path.read_bytes(),
        )
        for name, path in written_paths.items()
    ]

    snapshot = DeterministicConfigMerger().merge(
        sources,
        scope=EffectiveScope(
            workspace_id=OpaqueId("workspace-a"),
            channel_profile_id=OpaqueId("channel-a"),
            concept_profile_id=OpaqueId("concept-a"),
            episode_id=OpaqueId("episode-a"),
        ),
        versions=EffectiveVersions(
            core_distribution=__version__,
            core_contract=CORE_CONTRACT_VERSION,
            rules_version="rules-a",
            policy_version="policy-a/1.0",
            config_contract=CONFIG_CONTRACT_VERSION,
        ),
        core_lock_sha256=HashDigest("d" * 64),
        created_at="2026-01-02T03:04:05Z",
        runtime_override_allowlist=set(),
        extension_validators={},
    )

    assert snapshot.effective["settings"]["media"]["duration_seconds"] == 75
    digest = str(snapshot.effective_config_sha256)
    assert _SHA256.fullmatch(digest), digest

    policy = resolve_workflow_policy("standard")
    assert policy.mode.value == "standard"

    for name, path in written_paths.items():
        result = handle_validate(layer=layer_map[name].value, path=path)
        assert result.exit_code == 0, result.message
        assert result.payload is not None
        assert result.payload["ok"] is True

    doctor = handle_doctor()
    assert doctor.command == "doctor"
    assert doctor.payload is not None
    for field in (
        "core_version",
        "core_contract",
        "python_version",
        "purity_exit_code",
        "purity_summary",
        "purity_available",
    ):
        assert field in doctor.payload

    ledger = RetryIdempotencyLedger()
    first = handle_retry(
        "TASK-001",
        "standard",
        idempotency_key="pipeline-idem-1",
        ledger=ledger,
    )
    second = handle_retry(
        "TASK-001",
        "standard",
        idempotency_key="pipeline-idem-1",
        ledger=ledger,
    )
    assert first.exit_code == 0
    assert first.payload is not None
    assert first.payload["dispatched_new"] is True
    assert second.payload is not None
    assert second.payload["dispatched_new"] is False
    assert second.status == "duplicate"

    # --- Outside-tmp_path proof ---
    after = (
        marker.read_text(encoding="utf-8"),
        marker.stat().st_mtime_ns,
        marker.stat().st_size,
    )
    assert after == sealed_fingerprint

    # Every file created by this test body lives under tmp_path.
    for path in written_paths.values():
        assert path.is_relative_to(tmp_path)
        assert path.is_file()

    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert path.is_relative_to(tmp_path)
            # No absolute machine path leak in written config content.
            text = path.read_text(encoding="utf-8")
            assert "Boss" + "Kimu" not in text
            assert not re.search(r"(?i)\b[A-Za-z]:[\\/]", text)
