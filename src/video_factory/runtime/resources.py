"""Exact code projection for packaged W06 runtime/migration policy resources."""

from __future__ import annotations

import hashlib
from importlib.resources import files
import json
from typing import Mapping

from video_factory.config.canonical import canonical_sha256
from video_factory.json_boundary import parse_json_bytes, require_json_object


RESOURCE_PACKAGE = "video_factory.resources.runtime_migration"
RESOURCE_MANIFEST_VERSION = "runtime-migration-resource-manifest/1.0"
RESOURCE_FILENAMES = (
    "migration-registry-v1.json",
    "runtime-policy-v1.json",
)

_MIGRATABLE_LEGACY_VIEWS = (
    ("brief", "brief/1.0"),
    ("edit", "edit-manifest/1.0"),
    ("generation", "generation-packet/2.1"),
    ("storyboard", "storyboard/1.0"),
)
_SHADOW_ONLY_VIEWS = ("publish", "sound")


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
            "schema_version": "sqlite-execution-journal/1.2",
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
            "schema_version": "sqlite-migration-registry/1.1",
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
            "exact_legacy_anchor_required": True,
            "historical_rollback_target_forbidden": True,
            "production_activation_enabled": False,
        },
    }
    return {
        **identity,
        "policy_sha256": str(canonical_sha256(identity)),
    }


def _migration_registry() -> dict[str, object]:
    identity: dict[str, object] = {
        "registry_version": "projection-migration-registry/1.1",
        "fixture_only": True,
        "production_activation_enabled": False,
        "views": [
            {
                "view_kind": view_kind,
                "legacy_artifact_version": artifact_version,
                "consumer_status": "unregistered",
                "fixture_pinned_parity_required": True,
            }
            for view_kind, artifact_version in _MIGRATABLE_LEGACY_VIEWS
        ],
        "shadow_only_views": [
            {
                "view_kind": view_kind,
                "migration_status": "no_registered_legacy_contract",
            }
            for view_kind in _SHADOW_ONLY_VIEWS
        ],
    }
    return {
        **identity,
        "registry_sha256": str(canonical_sha256(identity)),
    }


def validate_runtime_migration_registry(
    registry: Mapping[str, object] | None = None,
) -> None:
    """Fail closed unless every migratable legacy view has a real schema.

    Blueprint still exposes six read-only design projections.  Only views with
    an independently registered legacy document contract may enter the W06
    exact-byte parity and cutover state machine.
    """

    from video_factory.artifacts import get_default_registry

    value = _migration_registry() if registry is None else dict(registry)
    if value.get("registry_version") != "projection-migration-registry/1.1":
        raise ValueError("unsupported projection migration registry version")
    views = value.get("views")
    shadow_only = value.get("shadow_only_views")
    if not isinstance(views, list) or not isinstance(shadow_only, list):
        raise ValueError("projection migration registry view sets are malformed")

    expected_views = dict(_MIGRATABLE_LEGACY_VIEWS)
    actual_views: dict[str, str] = {}
    schemas = get_default_registry()
    for item in views:
        if not isinstance(item, Mapping):
            raise ValueError("projection migration registry view is malformed")
        view_kind = item.get("view_kind")
        artifact_version = item.get("legacy_artifact_version")
        if not isinstance(view_kind, str) or not isinstance(artifact_version, str):
            raise ValueError("projection migration registry view identity is malformed")
        if view_kind in actual_views:
            raise ValueError("projection migration registry contains a duplicate view")
        if not schemas.has(artifact_version):
            raise ValueError(
                f"projection migration legacy schema is unregistered: {artifact_version}"
            )
        actual_views[view_kind] = artifact_version
    if actual_views != expected_views:
        raise ValueError("projection migration registry differs from target policy")

    actual_shadow = {
        item.get("view_kind")
        for item in shadow_only
        if isinstance(item, Mapping)
        and item.get("migration_status") == "no_registered_legacy_contract"
    }
    if actual_shadow != set(_SHADOW_ONLY_VIEWS):
        raise ValueError("shadow-only projection registry differs from target policy")
    if set(actual_views) & actual_shadow:
        raise ValueError("a projection view cannot be migratable and shadow-only")


def migratable_legacy_versions() -> dict[str, str]:
    """Return the exact target-owned migration view-to-contract mapping."""

    registry = _migration_registry()
    validate_runtime_migration_registry(registry)
    return {
        str(item["view_kind"]): str(item["legacy_artifact_version"])
        for item in registry["views"]
    }


def runtime_resource_documents() -> dict[str, dict[str, object]]:
    documents = {
        "migration-registry-v1.json": _migration_registry(),
        "runtime-policy-v1.json": _runtime_policy(),
    }
    validate_runtime_migration_registry(documents["migration-registry-v1.json"])
    return documents


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
    "migratable_legacy_versions",
    "runtime_resource_bytes",
    "runtime_resource_documents",
    "runtime_resource_manifest",
    "runtime_resource_manifest_bytes",
    "validate_runtime_migration_registry",
    "validate_packaged_runtime_resources",
]
