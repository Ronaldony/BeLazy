"""Exact code projection for packaged W06 runtime/migration policy resources."""

from __future__ import annotations

import hashlib
from importlib.resources import files
import json

from video_factory.config.canonical import canonical_sha256
from video_factory.json_boundary import parse_json_bytes, require_json_object


RESOURCE_PACKAGE = "video_factory.resources.runtime_migration"
RESOURCE_MANIFEST_VERSION = "runtime-migration-resource-manifest/1.0"
RESOURCE_FILENAMES = (
    "migration-registry-v1.json",
    "runtime-policy-v1.json",
)


def _render(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _runtime_policy() -> dict[str, object]:
    identity: dict[str, object] = {
        "policy_version": "runtime-policy/1.0",
        "runtime_package": "video_factory_runtime",
        "fixture_only": True,
        "production_enabled": False,
        "journal": {
            "backend": "sqlite",
            "schema_version": "sqlite-execution-journal/1.1",
            "synchronous": "FULL",
            "atomic_idempotency_claim": True,
            "request_digest_conflict_rejected": True,
            "hash_chain_required": True,
            "reconcile_only_states": [
                "dispatched",
                "dispatching",
                "partial",
                "reconciling",
                "uncertain",
            ],
            "resumable_pre_effect_states": ["authorized", "planned", "reserved"],
        },
        "authority": {
            "fresh_verification_purposes": ["dispatch", "reconcile"],
            "reservation_settlement_required": True,
            "kill_switch_rechecked_immediately_before_effect": True,
            "service_identity_attestation_required": True,
            "opaque_credential_handle_required": True,
        },
        "publication": {
            "eligible_release_status": "ready",
            "w05_authority_effect": "none",
            "side_effect_action_id": "ready_for_human_publish",
            "fixture_publisher_only": True,
            "blind_retry_forbidden": True,
        },
        "migration": {
            "modes": [
                "dual_read_compare",
                "legacy_only",
                "projection_read_only",
                "rolled_back",
            ],
            "projection_read_only": True,
            "projections_are_authority": False,
            "trusted_parity_receipt_required": True,
            "separate_activation_verification_required": True,
            "production_activation_enabled": False,
        },
    }
    return {
        **identity,
        "policy_sha256": str(canonical_sha256(identity)),
    }


def _migration_registry() -> dict[str, object]:
    identity: dict[str, object] = {
        "registry_version": "projection-migration-registry/1.0",
        "fixture_only": True,
        "production_activation_enabled": False,
        "views": [
            {
                "view_kind": "brief",
                "legacy_artifact_version": "brief/1.0",
                "consumer_status": "unregistered",
                "fixture_pinned_parity_required": True,
            },
            {
                "view_kind": "edit",
                "legacy_artifact_version": "edit-manifest/1.0",
                "consumer_status": "unregistered",
                "fixture_pinned_parity_required": True,
            },
            {
                "view_kind": "generation",
                "legacy_artifact_version": "generation-packet/2.1",
                "consumer_status": "unregistered",
                "fixture_pinned_parity_required": True,
            },
            {
                "view_kind": "publish",
                "legacy_artifact_version": "publish-manifest/1.0",
                "consumer_status": "unregistered",
                "fixture_pinned_parity_required": True,
            },
            {
                "view_kind": "sound",
                "legacy_artifact_version": "sound-manifest/1.0",
                "consumer_status": "unregistered",
                "fixture_pinned_parity_required": True,
            },
            {
                "view_kind": "storyboard",
                "legacy_artifact_version": "storyboard/1.0",
                "consumer_status": "unregistered",
                "fixture_pinned_parity_required": True,
            },
        ],
    }
    return {
        **identity,
        "registry_sha256": str(canonical_sha256(identity)),
    }


def runtime_resource_documents() -> dict[str, dict[str, object]]:
    return {
        "migration-registry-v1.json": _migration_registry(),
        "runtime-policy-v1.json": _runtime_policy(),
    }


def runtime_resource_bytes() -> dict[str, bytes]:
    return {
        name: _render(value) for name, value in runtime_resource_documents().items()
    }


def runtime_resource_manifest() -> dict[str, object]:
    payloads = runtime_resource_bytes()
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


def runtime_resource_manifest_bytes() -> bytes:
    return _render(runtime_resource_manifest())


def validate_packaged_runtime_resources() -> str:
    root = files(RESOURCE_PACKAGE)
    manifest_payload = root.joinpath(
        "runtime-migration-resource-manifest.json"
    ).read_bytes()
    manifest = require_json_object(parse_json_bytes(manifest_payload))
    if manifest != runtime_resource_manifest():
        raise ValueError("packaged runtime/migration manifest differs from code")
    expected = runtime_resource_bytes()
    actual_names = {
        item.name
        for item in root.iterdir()
        if item.is_file() and item.name.endswith(".json")
    }
    if actual_names != {
        *RESOURCE_FILENAMES,
        "runtime-migration-resource-manifest.json",
    }:
        raise ValueError("packaged runtime/migration resource set is not exact")
    for name, payload in expected.items():
        if root.joinpath(name).read_bytes() != payload:
            raise ValueError(f"packaged runtime/migration resource mismatch: {name}")
    return hashlib.sha256(manifest_payload).hexdigest()


__all__ = [
    "RESOURCE_FILENAMES",
    "RESOURCE_MANIFEST_VERSION",
    "runtime_resource_bytes",
    "runtime_resource_documents",
    "runtime_resource_manifest",
    "runtime_resource_manifest_bytes",
    "validate_packaged_runtime_resources",
]
