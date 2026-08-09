from __future__ import annotations

import hashlib
from importlib.resources import files

from video_factory.json_boundary import parse_json_bytes, require_json_object
from video_factory.quality import (
    quality_policy_from_mapping,
    target_quality_policy,
    validate_packaged_quality_resources,
)


def test_packaged_quality_policy_is_exact_code_projection() -> None:
    digest = validate_packaged_quality_resources()
    root = files("video_factory.resources.quality_release")
    manifest_payload = root.joinpath(
        "quality-release-resource-manifest.json"
    ).read_bytes()
    assert digest == hashlib.sha256(manifest_payload).hexdigest()
    manifest = require_json_object(parse_json_bytes(manifest_payload))
    assert manifest["manifest_version"] == "quality-release-resource-manifest/1.0"
    assert manifest["resource_count"] == 1
    entry = manifest["resources"][0]
    assert entry["filename"] == "automation-quality-policy-v1.json"
    payload = root.joinpath(entry["filename"]).read_bytes()
    assert hashlib.sha256(payload).hexdigest() == entry["sha256"]
    assert quality_policy_from_mapping(
        require_json_object(parse_json_bytes(payload))
    ) == target_quality_policy()
