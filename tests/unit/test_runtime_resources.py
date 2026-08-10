from __future__ import annotations

from copy import deepcopy
import hashlib
from importlib.resources import files

import pytest

from video_factory.artifacts import get_default_registry
from video_factory.runtime.resources import (
    RESOURCE_FILENAMES,
    runtime_resource_bytes,
    runtime_resource_documents,
    runtime_resource_manifest,
    runtime_resource_manifest_bytes,
    validate_runtime_migration_registry,
    validate_packaged_runtime_resources,
)


def test_runtime_resources_are_exact_safe_code_projection() -> None:
    documents = runtime_resource_documents()
    assert tuple(documents) == RESOURCE_FILENAMES
    policy = documents["runtime-policy-v1.json"]
    registry = documents["migration-registry-v1.json"]
    assert policy["fixture_only"] is True
    assert policy["production_enabled"] is False
    assert policy["migration"]["production_activation_enabled"] is False
    assert policy["migration"]["projections_are_authority"] is False
    assert registry["production_activation_enabled"] is False
    assert len(registry["views"]) == 4
    assert all(item["consumer_status"] == "unregistered" for item in registry["views"])
    assert {item["view_kind"] for item in registry["shadow_only_views"]} == {
        "publish",
        "sound",
    }
    schemas = get_default_registry()
    assert all(
        schemas.has(item["legacy_artifact_version"])
        for item in registry["views"]
    )
    validate_runtime_migration_registry(registry)

    root = files("video_factory.resources.runtime_migration")
    payloads = runtime_resource_bytes()
    for name, payload in payloads.items():
        assert root.joinpath(name).read_bytes() == payload
    manifest_payload = root.joinpath(
        "runtime-migration-resource-manifest.json"
    ).read_bytes()
    assert manifest_payload == runtime_resource_manifest_bytes()
    manifest = runtime_resource_manifest()
    assert manifest["resource_count"] == 2
    assert [item["filename"] for item in manifest["resources"]] == list(
        RESOURCE_FILENAMES
    )
    assert validate_packaged_runtime_resources() == hashlib.sha256(
        manifest_payload
    ).hexdigest()


def test_migration_registry_rejects_unregistered_legacy_contract() -> None:
    registry = deepcopy(runtime_resource_documents()["migration-registry-v1.json"])
    registry["views"][0]["legacy_artifact_version"] = "missing-legacy/1.0"

    with pytest.raises(ValueError, match="legacy schema is unregistered"):
        validate_runtime_migration_registry(registry)


def test_migration_registry_keeps_nonlegacy_design_views_out_of_cutover() -> None:
    registry = deepcopy(runtime_resource_documents()["migration-registry-v1.json"])
    registry["views"].append(
        {
            "view_kind": "sound",
            "legacy_artifact_version": "sound-manifest/1.0",
            "consumer_status": "unregistered",
            "fixture_pinned_parity_required": True,
        }
    )

    with pytest.raises(ValueError, match="legacy schema is unregistered"):
        validate_runtime_migration_registry(registry)
