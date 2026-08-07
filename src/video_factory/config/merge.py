"""Deterministic closed-config merge with leaf provenance and snapshot hashing."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
import re

from video_factory.domain import HashDigest
from video_factory.engine.mode import (
    ExecutionModeLimits,
    resolve_effective_execution_mode,
)

from .canonical import canonical_sha256
from .contracts import (
    CONFIG_LAYER_ORDER,
    ConfigLayer,
    ConfigSource,
    EffectiveBindings,
    EffectiveConfigSnapshot,
    EffectiveScope,
    EffectiveVersions,
    ExtensionValidator,
    ProvenanceEntry,
    SafetyConstraints,
    SourceRecord,
)
from .models import (
    CONFIG_CONTRACT_VERSION,
    EFFECTIVE_CONFIG_ARTIFACT_VERSION,
    ConfigValidationError,
    normalize_config_values,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LAYER_INDEX = {layer: index for index, layer in enumerate(CONFIG_LAYER_ORDER)}


class ConfigMergeError(ValueError):
    """Raised when individually valid layers cannot be merged safely."""


def _pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _child_pointer(parent: str, key: str) -> str:
    return f"{parent}/{_pointer_token(key)}"


def _json_kind(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float, Decimal)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "array"
    return type(value).__name__


def _remove_pointer_tree(provenance: dict[str, ProvenanceEntry], pointer: str) -> None:
    for recorded in tuple(provenance):
        if recorded == pointer or recorded.startswith(pointer + "/"):
            del provenance[recorded]


def _merge_core_object(
    target: dict[str, object],
    incoming: Mapping[str, object],
    parent_pointer: str,
    winner: ProvenanceEntry,
    provenance: dict[str, ProvenanceEntry],
) -> None:
    for key in sorted(incoming):
        value = incoming[key]
        pointer = _child_pointer(parent_pointer, key)
        existing = target.get(key)
        exists = key in target
        incoming_kind = _json_kind(value)

        if incoming_kind == "null":
            raise ConfigMergeError(f"null deletion is forbidden at {pointer}")
        if exists and _json_kind(existing) != incoming_kind:
            raise ConfigMergeError(
                f"type conflict at {pointer}: {_json_kind(existing)} versus {incoming_kind}"
            )

        if incoming_kind == "object":
            created = not exists
            if not exists:
                target[key] = {}
            child = target[key]
            if not isinstance(child, dict):
                child = dict(child)  # defensive copy for another Mapping implementation
                target[key] = child
            provenance.pop(pointer, None)
            if value:
                _merge_core_object(child, value, pointer, winner, provenance)
            elif created:
                provenance[pointer] = winner
            continue

        target[key] = deepcopy(value)
        _remove_pointer_tree(provenance, pointer)
        provenance[pointer] = winner


def _extension_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ConfigMergeError("validated extensions must be an object")
    return value


def _validate_extensions(
    extensions: Mapping[str, object],
    validators: Mapping[str, ExtensionValidator],
) -> None:
    for namespace, raw_extension in extensions.items():
        validator = validators.get(namespace)
        if validator is None:
            raise ConfigMergeError(f"unregistered extension namespace: {namespace}")
        extension = _extension_mapping(raw_extension)
        contract_version = extension["contract_version"]
        payload = _extension_mapping(extension["payload"])
        errors = validator.validate(str(contract_version), payload)
        if errors:
            raise ConfigMergeError(
                f"extension {namespace} failed its owner contract: {'; '.join(errors)}"
            )


def _merge_extensions(
    target: dict[str, object],
    incoming: Mapping[str, object],
    winner: ProvenanceEntry,
    provenance: dict[str, ProvenanceEntry],
) -> None:
    for namespace in sorted(incoming):
        pointer = _child_pointer("/extensions".removesuffix("/"), namespace)
        target[namespace] = deepcopy(incoming[namespace])
        _remove_pointer_tree(provenance, pointer)
        provenance[pointer] = winner


def _leaf_pointers(values: Mapping[str, object]) -> set[str]:
    leaves: set[str] = set()

    def visit(value: object, pointer: str) -> None:
        if pointer == "/extensions" and isinstance(value, Mapping):
            leaves.update(_child_pointer(pointer, namespace) for namespace in value)
            return
        if isinstance(value, Mapping):
            if not value:
                leaves.add(pointer)
                return
            for key, child in value.items():
                visit(child, _child_pointer(pointer, key))
            return
        leaves.add(pointer)

    for root, value in values.items():
        visit(value, f"/{_pointer_token(root)}")
    return leaves


def _validate_source_metadata(source: ConfigSource) -> None:
    if not isinstance(source.source_id, str) or not source.source_id:
        raise ConfigMergeError("source_id must be a non-empty opaque identifier")
    if _SHA256.fullmatch(source.sha256) is None:
        raise ConfigMergeError(f"source {source.source_id} has an invalid SHA-256 digest")
    path = str(source.path)
    if not path or "\\" in path or path.startswith("/"):
        raise ConfigMergeError(f"source {source.source_id} path is not relative POSIX")
    path_body = path.removeprefix("package:")
    if not path_body or any(part in {"", ".", ".."} for part in path_body.split("/")):
        raise ConfigMergeError(f"source {source.source_id} path is not contained")
    if ":" in path_body:
        raise ConfigMergeError(f"source {source.source_id} path is not relative POSIX")


def _validate_timestamp(value: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ConfigMergeError("created_at must be an RFC 3339 date-time") from error
    if parsed.tzinfo is None:
        raise ConfigMergeError("created_at must include a UTC offset")


def _source_record(source: ConfigSource) -> SourceRecord:
    return SourceRecord(source.layer, source.source_id, source.path, source.sha256)


def snapshot_to_mapping(snapshot: EffectiveConfigSnapshot) -> dict[str, object]:
    """Return the JSON Schema representation of an effective snapshot."""

    return {
        "artifact_version": snapshot.artifact_version,
        "created_at": snapshot.created_at,
        "scope": {
            "workspace_id": snapshot.scope.workspace_id,
            "channel_profile_id": snapshot.scope.channel_profile_id,
            "concept_profile_id": snapshot.scope.concept_profile_id,
            "episode_id": snapshot.scope.episode_id,
        },
        "versions": {
            "core_distribution": snapshot.versions.core_distribution,
            "core_contract": snapshot.versions.core_contract,
            "rules_version": snapshot.versions.rules_version,
            "policy_version": snapshot.versions.policy_version,
            "config_contract": snapshot.versions.config_contract,
        },
        "bindings": {"core_lock_sha256": snapshot.bindings.core_lock_sha256},
        "sources": [
            {
                "layer": source.layer.value,
                "source_id": source.source_id,
                "path": source.path,
                "sha256": source.sha256,
            }
            for source in snapshot.sources
        ],
        "effective": deepcopy(dict(snapshot.effective)),
        "provenance": {
            pointer: {
                "winning_layer": entry.winning_layer.value,
                "source_id": entry.source_id,
            }
            for pointer, entry in snapshot.provenance.items()
        },
        "constraints": {
            "applied": list(snapshot.constraints.applied),
            "rejected_overrides": list(snapshot.constraints.rejected_overrides),
        },
        "effective_config_sha256": snapshot.effective_config_sha256,
    }


class DeterministicConfigMerger:
    """Strict recursive merge; arrays replace and extension payloads stay opaque."""

    def merge(
        self,
        sources: Sequence[ConfigSource],
        *,
        scope: EffectiveScope,
        versions: EffectiveVersions,
        core_lock_sha256: HashDigest,
        created_at: str,
        runtime_override_allowlist: Collection[str],
        extension_validators: Mapping[str, ExtensionValidator],
        execution_mode_limits: ExecutionModeLimits | None = None,
    ) -> EffectiveConfigSnapshot:
        _validate_timestamp(created_at)
        if not sources:
            raise ConfigMergeError("at least one configuration source is required")
        if _SHA256.fullmatch(core_lock_sha256) is None:
            raise ConfigMergeError("core_lock_sha256 must contain 64 lowercase hexadecimal digits")
        if versions.config_contract != CONFIG_CONTRACT_VERSION:
            raise ConfigMergeError(
                f"config_contract must be {CONFIG_CONTRACT_VERSION}, got {versions.config_contract}"
            )
        if not all(
            (
                versions.core_distribution,
                versions.core_contract,
                versions.rules_version,
                versions.policy_version,
            )
        ):
            raise ConfigMergeError("all effective version fields must be non-empty")

        allowlist = set(runtime_override_allowlist)
        if not all(isinstance(pointer, str) and pointer.startswith("/") for pointer in allowlist):
            raise ConfigMergeError("runtime override allowlist entries must be JSON Pointers")

        seen_layers: set[ConfigLayer] = set()
        seen_source_ids: set[str] = set()
        normalized_sources: list[tuple[ConfigSource, dict[str, object]]] = []
        for source in sources:
            _validate_source_metadata(source)
            if source.layer in seen_layers:
                raise ConfigMergeError(f"multiple sources supplied for layer {source.layer.value}")
            if source.source_id in seen_source_ids:
                raise ConfigMergeError(f"duplicate source_id: {source.source_id}")
            seen_layers.add(source.layer)
            seen_source_ids.add(source.source_id)
            try:
                normalized = normalize_config_values(source.values)
            except ConfigValidationError as error:
                raise ConfigMergeError(f"source {source.source_id}: {error}") from error
            extensions = _extension_mapping(normalized.get("extensions", {}))
            _validate_extensions(extensions, extension_validators)
            if source.layer is ConfigLayer.RUNTIME_OVERRIDE:
                disallowed = sorted(_leaf_pointers(normalized) - allowlist)
                if disallowed:
                    raise ConfigMergeError(
                        "runtime override contains disallowed fields: " + ", ".join(disallowed)
                    )
            normalized_sources.append((source, normalized))

        normalized_sources.sort(key=lambda item: _LAYER_INDEX[item[0].layer])
        effective: dict[str, object] = {"settings": {}, "extensions": {}}
        provenance: dict[str, ProvenanceEntry] = {}
        for source, values in normalized_sources:
            winner = ProvenanceEntry(source.layer, source.source_id)
            settings = values.get("settings")
            if settings is not None:
                _merge_core_object(
                    effective["settings"],
                    _extension_mapping(settings),
                    "/settings".removesuffix("/"),
                    winner,
                    provenance,
                )
            extensions = values.get("extensions")
            if extensions is not None:
                _merge_extensions(
                    effective["extensions"],
                    _extension_mapping(extensions),
                    winner,
                    provenance,
                )

        settings = effective["settings"]
        if not isinstance(settings, dict):
            raise ConfigMergeError("validated settings must be an object")
        execution = settings.get("execution")
        raw_requested_mode: object | None = None
        if isinstance(execution, Mapping):
            raw_requested_mode = execution.get("mode")

        limits = execution_mode_limits or ExecutionModeLimits()
        if limits.mode_maximum is None:
            limits = replace(limits, mode_maximum=raw_requested_mode)
        mode_decision = resolve_effective_execution_mode(limits)
        if execution is None:
            settings["execution"] = {"mode": mode_decision.effective_mode.value}
        elif isinstance(execution, dict):
            execution["mode"] = mode_decision.effective_mode.value
        else:
            replacement = dict(execution)
            replacement["mode"] = mode_decision.effective_mode.value
            settings["execution"] = replacement

        mode_constraints = (
            f"execution_mode:channel={mode_decision.channel_maximum.value}",
            f"execution_mode:mode={mode_decision.mode_maximum.value}",
            f"execution_mode:adapter={mode_decision.adapter_maximum.value}",
            f"execution_mode:effective={mode_decision.effective_mode.value}",
            *(f"execution_mode:fail_safe={source}" for source in mode_decision.fail_safe_sources),
        )
        constraints = SafetyConstraints(
            applied=(
                *tuple(
                    f"runtime_override_allowlist:{pointer}" for pointer in sorted(allowlist)
                ),
                *mode_constraints,
            ),
            rejected_overrides=(),
        )
        draft = EffectiveConfigSnapshot(
            artifact_version=str(EFFECTIVE_CONFIG_ARTIFACT_VERSION),
            created_at=created_at,
            scope=scope,
            versions=versions,
            bindings=EffectiveBindings(core_lock_sha256),
            sources=tuple(_source_record(source) for source, _ in normalized_sources),
            effective=effective,
            provenance=dict(sorted(provenance.items())),
            constraints=constraints,
            effective_config_sha256=HashDigest("0" * 64),
        )
        hash_payload = snapshot_to_mapping(draft)
        del hash_payload["created_at"]
        del hash_payload["effective_config_sha256"]
        return replace(draft, effective_config_sha256=canonical_sha256(hash_payload))
