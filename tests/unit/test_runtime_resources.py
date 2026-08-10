from __future__ import annotations

import hashlib
from importlib.resources import files

from video_factory.runtime.resources import (
    RESOURCE_FILENAMES,
    runtime_resource_bytes,
    runtime_resource_documents,
    runtime_resource_manifest,
    runtime_resource_manifest_bytes,
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
    assert len(registry["views"]) == 6
    assert all(item["consumer_status"] == "unregistered" for item in registry["views"])

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
