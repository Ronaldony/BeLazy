"""Typed, closed models for the four persisted configuration layers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal
import math
import re
from typing import TypeAlias

from video_factory.domain import ArtifactVersion, OpaqueId, RelativeArtifactPath
from video_factory.json_boundary import (
    JsonInputError,
    parse_json_bytes,
    require_json_object,
)

from .canonical import canonical_json_bytes, canonical_sha256
from .contracts import ConfigLayer, ConfigSource, ExtensionPayload


CONFIG_CONTRACT_VERSION = "1.0"
WORKSPACE_CONFIG_ARTIFACT_VERSION = ArtifactVersion("workspace-config/1.0")
CHANNEL_CONFIG_ARTIFACT_VERSION = ArtifactVersion("channel-config/1.0")
CONCEPT_CONFIG_ARTIFACT_VERSION = ArtifactVersion("concept-config/1.0")
EPISODE_CONFIG_ARTIFACT_VERSION = ArtifactVersion("episode-config/1.0")
EFFECTIVE_CONFIG_ARTIFACT_VERSION = ArtifactVersion("effective-config/1.0")

_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_EXTENSION_NAMESPACE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_CONTRACT_VERSION = re.compile(r"^[1-9][0-9]*\.[0-9]+$")


class ConfigValidationError(ValueError):
    """Raised when core-owned configuration is not closed or well typed."""


@dataclass(frozen=True, slots=True)
class AspectRatio:
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class MediaSettings:
    duration_seconds: int | float | Decimal | None = None
    aspect_ratio: AspectRatio | None = None
    platforms: tuple[OpaqueId, ...] | None = None


@dataclass(frozen=True, slots=True)
class IdentitySettings:
    """An empty tuple explicitly represents no recurring identities."""

    recurring_character_ids: tuple[OpaqueId, ...]


@dataclass(frozen=True, slots=True)
class ExecutionSettings:
    """An opaque requested mode; the core defines no production default here."""

    mode: OpaqueId


@dataclass(frozen=True, slots=True)
class CoreSettings:
    media: MediaSettings | None = None
    identity: IdentitySettings | None = None
    execution: ExecutionSettings | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceConfig:
    artifact_version: ArtifactVersion
    config_contract: str
    workspace_id: OpaqueId
    settings: CoreSettings = field(default_factory=CoreSettings)
    extensions: Mapping[str, ExtensionPayload] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChannelConfig:
    artifact_version: ArtifactVersion
    config_contract: str
    channel_id: OpaqueId
    settings: CoreSettings = field(default_factory=CoreSettings)
    extensions: Mapping[str, ExtensionPayload] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConceptConfig:
    artifact_version: ArtifactVersion
    config_contract: str
    concept_id: OpaqueId
    settings: CoreSettings = field(default_factory=CoreSettings)
    extensions: Mapping[str, ExtensionPayload] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EpisodeConfig:
    artifact_version: ArtifactVersion
    config_contract: str
    episode_id: OpaqueId
    settings: CoreSettings = field(default_factory=CoreSettings)
    extensions: Mapping[str, ExtensionPayload] = field(default_factory=dict)


LayerConfig: TypeAlias = WorkspaceConfig | ChannelConfig | ConceptConfig | EpisodeConfig

_DOCUMENT_SPECS: Mapping[ConfigLayer, tuple[ArtifactVersion, str, type[LayerConfig]]] = {
    ConfigLayer.WORKSPACE: (
        WORKSPACE_CONFIG_ARTIFACT_VERSION,
        "workspace_id",
        WorkspaceConfig,
    ),
    ConfigLayer.CHANNEL: (CHANNEL_CONFIG_ARTIFACT_VERSION, "channel_id", ChannelConfig),
    ConfigLayer.CONCEPT: (CONCEPT_CONFIG_ARTIFACT_VERSION, "concept_id", ConceptConfig),
    ConfigLayer.EPISODE: (EPISODE_CONFIG_ARTIFACT_VERSION, "episode_id", EpisodeConfig),
}


def _fail(message: str) -> ConfigValidationError:
    return ConfigValidationError(message)


def _expect_mapping(value: object, pointer: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise _fail(f"{pointer} must be an object with string keys")
    return value


def _reject_unknown(
    value: Mapping[str, object],
    allowed: set[str],
    pointer: str,
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise _fail(f"{pointer} contains unknown fields: {', '.join(unknown)}")


def _opaque_id(value: object, pointer: str) -> OpaqueId:
    if not isinstance(value, str) or _OPAQUE_ID.fullmatch(value) is None:
        raise _fail(f"{pointer} must be a non-empty opaque identifier")
    return OpaqueId(value)


def _positive_number(value: object, pointer: str) -> int | float | Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise _fail(f"{pointer} must be a number")
    if isinstance(value, float) and not math.isfinite(value):
        raise _fail(f"{pointer} must be finite")
    if isinstance(value, Decimal) and not value.is_finite():
        raise _fail(f"{pointer} must be finite")
    if value <= 0:
        raise _fail(f"{pointer} must be greater than zero")
    return value


def _parse_media(value: object) -> MediaSettings:
    media = _expect_mapping(value, "/settings/media")
    _reject_unknown(media, {"duration_seconds", "aspect_ratio", "platforms"}, "/settings/media")

    duration = None
    if "duration_seconds" in media:
        duration = _positive_number(media["duration_seconds"], "/settings/media/duration_seconds")

    ratio = None
    if "aspect_ratio" in media:
        raw_ratio = _expect_mapping(media["aspect_ratio"], "/settings/media/aspect_ratio")
        _reject_unknown(raw_ratio, {"width", "height"}, "/settings/media/aspect_ratio")
        if set(raw_ratio) != {"width", "height"}:
            raise _fail("/settings/media/aspect_ratio requires width and height")
        width = raw_ratio["width"]
        height = raw_ratio["height"]
        if (
            isinstance(width, bool)
            or isinstance(height, bool)
            or not isinstance(width, int)
            or not isinstance(height, int)
            or width <= 0
            or height <= 0
        ):
            raise _fail("/settings/media/aspect_ratio dimensions must be positive integers")
        ratio = AspectRatio(width=width, height=height)

    platforms = None
    if "platforms" in media:
        raw_platforms = media["platforms"]
        if (
            not isinstance(raw_platforms, Sequence)
            or isinstance(raw_platforms, (str, bytes, bytearray))
            or not raw_platforms
        ):
            raise _fail("/settings/media/platforms must be a non-empty array")
        platforms = tuple(
            _opaque_id(item, f"/settings/media/platforms/{index}")
            for index, item in enumerate(raw_platforms)
        )
        if len(set(platforms)) != len(platforms):
            raise _fail("/settings/media/platforms must contain unique values")

    return MediaSettings(
        duration_seconds=duration,
        aspect_ratio=ratio,
        platforms=platforms,
    )


def _parse_identity(value: object) -> IdentitySettings:
    identity = _expect_mapping(value, "/settings/identity")
    _reject_unknown(identity, {"recurring_character_ids"}, "/settings/identity")
    if "recurring_character_ids" not in identity:
        raise _fail("/settings/identity requires recurring_character_ids")
    raw_ids = identity["recurring_character_ids"]
    if not isinstance(raw_ids, Sequence) or isinstance(raw_ids, (str, bytes, bytearray)):
        raise _fail("/settings/identity/recurring_character_ids must be an array")
    identifiers = tuple(
        _opaque_id(item, f"/settings/identity/recurring_character_ids/{index}")
        for index, item in enumerate(raw_ids)
    )
    if len(set(identifiers)) != len(identifiers):
        raise _fail("/settings/identity/recurring_character_ids must contain unique values")
    return IdentitySettings(recurring_character_ids=identifiers)


def _parse_execution(value: object) -> ExecutionSettings:
    execution = _expect_mapping(value, "/settings/execution")
    _reject_unknown(execution, {"mode"}, "/settings/execution")
    if "mode" not in execution:
        raise _fail("/settings/execution requires mode")
    return ExecutionSettings(mode=_opaque_id(execution["mode"], "/settings/execution/mode"))


def _parse_settings(value: object) -> CoreSettings:
    settings = _expect_mapping(value, "/settings")
    _reject_unknown(settings, {"media", "identity", "execution"}, "/settings")
    return CoreSettings(
        media=_parse_media(settings["media"]) if "media" in settings else None,
        identity=_parse_identity(settings["identity"]) if "identity" in settings else None,
        execution=_parse_execution(settings["execution"]) if "execution" in settings else None,
    )


def _parse_extensions(value: object) -> Mapping[str, ExtensionPayload]:
    extensions = _expect_mapping(value, "/extensions")
    parsed: dict[str, ExtensionPayload] = {}
    for namespace, raw_extension in extensions.items():
        if _EXTENSION_NAMESPACE.fullmatch(namespace) is None:
            raise _fail(f"invalid extension namespace: {namespace}")
        extension = _expect_mapping(raw_extension, f"/extensions/{namespace}")
        _reject_unknown(extension, {"contract_version", "payload"}, f"/extensions/{namespace}")
        if set(extension) != {"contract_version", "payload"}:
            raise _fail(f"/extensions/{namespace} requires contract_version and payload")
        contract_version = extension["contract_version"]
        if not isinstance(contract_version, str) or _CONTRACT_VERSION.fullmatch(contract_version) is None:
            raise _fail(f"/extensions/{namespace}/contract_version is invalid")
        payload = _expect_mapping(extension["payload"], f"/extensions/{namespace}/payload")
        canonical_json_bytes(payload)
        parsed[namespace] = ExtensionPayload(contract_version, deepcopy(dict(payload)))
    return parsed


def normalize_config_values(values: Mapping[str, object]) -> dict[str, object]:
    """Validate the only core-owned merge roots and return JSON-compatible values."""

    values = _expect_mapping(values, "/")
    _reject_unknown(values, {"settings", "extensions"}, "/")
    normalized: dict[str, object] = {}
    if "settings" in values:
        normalized["settings"] = settings_to_mapping(_parse_settings(values["settings"]))
    if "extensions" in values:
        normalized["extensions"] = extensions_to_mapping(_parse_extensions(values["extensions"]))
    return normalized


def settings_to_mapping(settings: CoreSettings) -> dict[str, object]:
    result: dict[str, object] = {}
    if settings.media is not None:
        media: dict[str, object] = {}
        if settings.media.duration_seconds is not None:
            media["duration_seconds"] = settings.media.duration_seconds
        if settings.media.aspect_ratio is not None:
            media["aspect_ratio"] = {
                "width": settings.media.aspect_ratio.width,
                "height": settings.media.aspect_ratio.height,
            }
        if settings.media.platforms is not None:
            media["platforms"] = list(settings.media.platforms)
        result["media"] = media
    if settings.identity is not None:
        result["identity"] = {
            "recurring_character_ids": list(settings.identity.recurring_character_ids)
        }
    if settings.execution is not None:
        result["execution"] = {"mode": settings.execution.mode}
    return result


def extensions_to_mapping(extensions: Mapping[str, ExtensionPayload]) -> dict[str, object]:
    return {
        namespace: {
            "contract_version": extension.contract_version,
            "payload": deepcopy(dict(extension.payload)),
        }
        for namespace, extension in extensions.items()
    }


def config_document_from_mapping(
    data: Mapping[str, object],
    layer: ConfigLayer,
) -> LayerConfig:
    """Validate and type one persisted workspace, channel, concept, or episode document."""

    if layer not in _DOCUMENT_SPECS:
        raise _fail(f"{layer.value} is not a persisted configuration layer")
    artifact_version, id_key, model = _DOCUMENT_SPECS[layer]
    document = _expect_mapping(data, "/")
    allowed = {"artifact_version", "config_contract", id_key, "settings", "extensions"}
    _reject_unknown(document, allowed, "/")
    required = {"artifact_version", "config_contract", id_key}
    missing = sorted(required - set(document))
    if missing:
        raise _fail(f"/ is missing required fields: {', '.join(missing)}")
    if document["artifact_version"] != artifact_version:
        raise _fail(f"artifact_version must be {artifact_version}")
    if document["config_contract"] != CONFIG_CONTRACT_VERSION:
        raise _fail(f"config_contract must be {CONFIG_CONTRACT_VERSION}")

    settings = _parse_settings(document.get("settings", {}))
    extensions = _parse_extensions(document.get("extensions", {}))
    return model(
        artifact_version=artifact_version,
        config_contract=CONFIG_CONTRACT_VERSION,
        **{id_key: _opaque_id(document[id_key], f"/{id_key}")},
        settings=settings,
        extensions=extensions,
    )


def _parse_config_mapping(payload: bytes) -> Mapping[str, object]:
    try:
        return require_json_object(
            parse_json_bytes(payload, decimal_numbers=True)
        )
    except JsonInputError as error:
        raise _fail(f"configuration JSON rejected [{error.code.value}]: {error.detail}") from error


def parse_config_document(payload: bytes, layer: ConfigLayer) -> LayerConfig:
    data = _parse_config_mapping(payload)
    return config_document_from_mapping(_expect_mapping(data, "/"), layer)


def config_document_values(document: LayerConfig) -> dict[str, object]:
    return {
        "settings": settings_to_mapping(document.settings),
        "extensions": extensions_to_mapping(document.extensions),
    }


def config_source_from_document_bytes(
    *,
    layer: ConfigLayer,
    path: str,
    payload: bytes,
) -> ConfigSource:
    """Build a source whose digest is independent of JSON key order and formatting."""

    parsed_mapping = _parse_config_mapping(payload)
    document = config_document_from_mapping(parsed_mapping, layer)
    _, id_key, _ = _DOCUMENT_SPECS[layer]
    return ConfigSource(
        layer=layer,
        source_id=_opaque_id(parsed_mapping[id_key], f"/{id_key}"),
        path=RelativeArtifactPath(path),
        sha256=canonical_sha256(parsed_mapping),
        values=config_document_values(document),
    )
