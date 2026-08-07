"""Draft configuration document builders (plan-only; no disk writes).

Returns schema-valid config document mappings. Callers persist if desired.
"""

from __future__ import annotations

from typing import Mapping

from .contracts import ConfigLayer
from .models import (
    CHANNEL_CONFIG_ARTIFACT_VERSION,
    CONCEPT_CONFIG_ARTIFACT_VERSION,
    CONFIG_CONTRACT_VERSION,
    EPISODE_CONFIG_ARTIFACT_VERSION,
    ConfigValidationError,
    config_document_from_mapping,
    extensions_to_mapping,
    settings_to_mapping,
)


class ConfigDraftError(ValueError):
    """Raised when a draft config document cannot be validated."""


def _layer_document(
    layer: ConfigLayer,
    scope_id: str,
    *,
    settings: Mapping[str, object] | None = None,
    extensions: Mapping[str, object] | None = None,
) -> dict[str, object]:
    version_map = {
        ConfigLayer.CHANNEL: CHANNEL_CONFIG_ARTIFACT_VERSION,
        ConfigLayer.CONCEPT: CONCEPT_CONFIG_ARTIFACT_VERSION,
        ConfigLayer.EPISODE: EPISODE_CONFIG_ARTIFACT_VERSION,
    }
    id_key_map = {
        ConfigLayer.CHANNEL: "channel_id",
        ConfigLayer.CONCEPT: "concept_id",
        ConfigLayer.EPISODE: "episode_id",
    }
    if layer not in version_map:
        raise ConfigDraftError(f"unsupported draft layer: {layer}")

    id_key = id_key_map[layer]
    document: dict[str, object] = {
        "artifact_version": str(version_map[layer]),
        "config_contract": CONFIG_CONTRACT_VERSION,
        id_key: scope_id,
        "settings": dict(settings) if settings is not None else {},
        "extensions": dict(extensions) if extensions is not None else {},
    }
    try:
        parsed = config_document_from_mapping(document, layer)
    except ConfigValidationError as error:
        raise ConfigDraftError(str(error)) from error

    # Re-emit normalized mapping so callers get a clean, validated document.
    return {
        "artifact_version": str(parsed.artifact_version),
        "config_contract": parsed.config_contract,
        id_key: str(getattr(parsed, id_key)),
        "settings": settings_to_mapping(parsed.settings),
        "extensions": extensions_to_mapping(parsed.extensions),
    }


def draft_channel_config(
    channel_id: str,
    *,
    settings: Mapping[str, object] | None = None,
    extensions: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build a validated channel-config/1.0 document mapping (no write)."""

    return _layer_document(
        ConfigLayer.CHANNEL,
        channel_id,
        settings=settings,
        extensions=extensions,
    )


def draft_concept_config(
    concept_id: str,
    *,
    settings: Mapping[str, object] | None = None,
    extensions: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build a validated concept-config/1.0 document mapping (no write)."""

    return _layer_document(
        ConfigLayer.CONCEPT,
        concept_id,
        settings=settings,
        extensions=extensions,
    )


def draft_episode_config(
    episode_id: str,
    *,
    settings: Mapping[str, object] | None = None,
    extensions: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build a validated episode-config/1.0 document mapping (no write)."""

    return _layer_document(
        ConfigLayer.EPISODE,
        episode_id,
        settings=settings,
        extensions=extensions,
    )
