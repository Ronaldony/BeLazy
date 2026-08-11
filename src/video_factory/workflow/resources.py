"""Exact code projections for packaged W04 workflow/authority resources."""

from __future__ import annotations

import hashlib
from importlib.resources import files
from collections.abc import Mapping

from video_factory.config import canonical_json_bytes
from video_factory.json_boundary import parse_json_bytes, require_json_object

from .definition import default_workflow_definition, workflow_definition_to_mapping
from .parity import default_parity_normalization


RESOURCE_PACKAGE = "video_factory.resources.workflow_authority"
RESOURCE_MANIFEST_VERSION = "workflow-authority-resource-manifest/1.0"
AUTHORITY_POLICY_BUNDLE_SHA256 = (
    "6b4192d4cba4f2351031d972165ccb0d156b43340f2aa52c10b0d54f738f2dbd"
)
RESOURCE_FILENAMES = (
    "authority-policy-v2.1.json",
    "episode-production-workflow.json",
    "legacy-parity-normalization.json",
)


def _authority_policy_document() -> dict[str, object]:
    """Load the pinned policy resource without a workflow -> authority edge."""

    payload = files(RESOURCE_PACKAGE).joinpath("authority-policy-v2.1.json").read_bytes()
    value = dict(require_json_object(parse_json_bytes(payload)))
    identity = dict(value)
    bundle_id = identity.pop("bundle_id", None)
    bundle_sha256 = identity.pop("bundle_sha256", None)
    observed_sha256 = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
    if (
        value.get("artifact_version") != "policy-bundle/1.0"
        or value.get("policy_version") != "authority-policy/2.1"
        or value.get("default_decision") != "denied"
        or value.get("unknown_state_fail_closed") is not True
        or value.get("self_approval_forbidden") is not True
        or value.get("release_campaign_enabled") is not False
        or value.get("maximum_r4_validity_seconds") != 300
        or bundle_sha256 != AUTHORITY_POLICY_BUNDLE_SHA256
        or observed_sha256 != AUTHORITY_POLICY_BUNDLE_SHA256
        or bundle_id != f"policy-bundle-{AUTHORITY_POLICY_BUNDLE_SHA256[:20]}"
    ):
        raise ValueError("packaged authority policy is not the target-owned projection")
    return value


def _render(value: object) -> bytes:
    import json

    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def workflow_resource_documents() -> dict[str, dict[str, object]]:
    return {
        "authority-policy-v2.1.json": _authority_policy_document(),
        "episode-production-workflow.json": workflow_definition_to_mapping(
            default_workflow_definition()
        ),
        "legacy-parity-normalization.json": default_parity_normalization(),
    }


def workflow_resource_bytes() -> dict[str, bytes]:
    return {name: _render(value) for name, value in workflow_resource_documents().items()}


def workflow_resource_manifest() -> dict[str, object]:
    payloads = workflow_resource_bytes()
    return {
        "manifest_version": RESOURCE_MANIFEST_VERSION,
        "resource_count": len(payloads),
        "resources": [
            {"filename": name, "sha256": hashlib.sha256(payloads[name]).hexdigest()}
            for name in RESOURCE_FILENAMES
        ],
    }


def validate_packaged_workflow_resources() -> str:
    root = files(RESOURCE_PACKAGE)
    manifest_payload = root.joinpath("workflow-authority-resource-manifest.json").read_bytes()
    manifest = require_json_object(parse_json_bytes(manifest_payload))
    if manifest != workflow_resource_manifest():
        raise ValueError("packaged workflow authority manifest does not match code projection")
    expected = workflow_resource_bytes()
    actual_names = {
        item.name
        for item in root.iterdir()
        if item.is_file() and item.name.endswith(".json")
    }
    if actual_names != {*RESOURCE_FILENAMES, "workflow-authority-resource-manifest.json"}:
        raise ValueError("packaged workflow authority resource set is not exact")
    for name, payload in expected.items():
        if root.joinpath(name).read_bytes() != payload:
            raise ValueError(f"packaged workflow authority resource mismatch: {name}")
    return hashlib.sha256(manifest_payload).hexdigest()


def workflow_resource_semantic_sha256(value: Mapping[str, object]) -> str:
    """Canonical semantic digest used only for diagnostics."""

    return hashlib.sha256(canonical_json_bytes(dict(value))).hexdigest()
