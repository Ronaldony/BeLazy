"""Exact code projection for the packaged W05 quality/release policy."""

from __future__ import annotations

import hashlib
from importlib.resources import files
import json

from video_factory.json_boundary import parse_json_bytes, require_json_object

from .policy import quality_policy_to_mapping, target_quality_policy


RESOURCE_PACKAGE = "video_factory.resources.quality_release"
RESOURCE_MANIFEST_VERSION = "quality-release-resource-manifest/1.0"
RESOURCE_FILENAMES = ("automation-quality-policy-v1.json",)


def _render(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def quality_resource_documents() -> dict[str, dict[str, object]]:
    return {
        "automation-quality-policy-v1.json": quality_policy_to_mapping(
            target_quality_policy()
        )
    }


def quality_resource_bytes() -> dict[str, bytes]:
    return {
        name: _render(value)
        for name, value in quality_resource_documents().items()
    }


def quality_resource_manifest() -> dict[str, object]:
    payloads = quality_resource_bytes()
    return {
        "manifest_version": RESOURCE_MANIFEST_VERSION,
        "resource_count": len(payloads),
        "resources": [
            {
                "filename": name,
                "sha256": hashlib.sha256(payloads[name]).hexdigest(),
            }
            for name in RESOURCE_FILENAMES
        ],
    }


def quality_resource_manifest_bytes() -> bytes:
    return _render(quality_resource_manifest())


def validate_packaged_quality_resources() -> str:
    root = files(RESOURCE_PACKAGE)
    manifest_payload = root.joinpath(
        "quality-release-resource-manifest.json"
    ).read_bytes()
    manifest = require_json_object(parse_json_bytes(manifest_payload))
    if manifest != quality_resource_manifest():
        raise ValueError(
            "packaged quality/release manifest does not match code projection"
        )
    expected = quality_resource_bytes()
    actual_names = {
        item.name
        for item in root.iterdir()
        if item.is_file() and item.name.endswith(".json")
    }
    if actual_names != {
        *RESOURCE_FILENAMES,
        "quality-release-resource-manifest.json",
    }:
        raise ValueError("packaged quality/release resource set is not exact")
    for name, payload in expected.items():
        if root.joinpath(name).read_bytes() != payload:
            raise ValueError(f"packaged quality/release resource mismatch: {name}")
    return hashlib.sha256(manifest_payload).hexdigest()


__all__ = [
    "RESOURCE_FILENAMES",
    "RESOURCE_MANIFEST_VERSION",
    "quality_resource_bytes",
    "quality_resource_documents",
    "quality_resource_manifest",
    "quality_resource_manifest_bytes",
    "validate_packaged_quality_resources",
]
