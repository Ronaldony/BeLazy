from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path

import pytest

from video_factory import CORE_CONTRACT_VERSION, __version__
from video_factory.config import (
    CONFIG_CONTRACT_VERSION,
    ChannelConfig,
    ConceptConfig,
    ConfigLayer,
    ConfigMergeError,
    ConfigSource,
    DeterministicConfigMerger,
    EffectiveScope,
    EffectiveVersions,
    canonical_sha256,
    config_source_from_document_bytes,
    parse_config_document,
)
from video_factory.domain import HashDigest, OpaqueId, RelativeArtifactPath


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
LAYER_FIXTURES = (
    (ConfigLayer.WORKSPACE, "workspace_config.json"),
    (ConfigLayer.CHANNEL, "channel_config.json"),
    (ConfigLayer.CONCEPT, "concept_config.json"),
    (ConfigLayer.EPISODE, "episode_config.json"),
)


class SyntheticExtensionValidator:
    def validate(self, contract_version: str, payload: dict[str, object]) -> tuple[str, ...]:
        if contract_version != "1.0":
            return ("unexpected contract",)
        if "owner_field" not in payload:
            return ("owner_field is required",)
        return ()


def _direct_source(
    layer: ConfigLayer,
    source_id: str,
    values: dict[str, object],
) -> ConfigSource:
    source_document = {"layer": layer.value, "source_id": source_id, "values": values}
    path = "package:config/defaults.json" if layer is ConfigLayer.CORE_DEFAULTS else f"config/{source_id}.json"
    return ConfigSource(
        layer=layer,
        source_id=OpaqueId(source_id),
        path=RelativeArtifactPath(path),
        sha256=canonical_sha256(source_document),
        values=values,
    )


def _persisted_sources() -> list[ConfigSource]:
    return [
        config_source_from_document_bytes(
            layer=layer,
            path=f"tests/fixtures/{filename}",
            payload=(FIXTURES / filename).read_bytes(),
        )
        for layer, filename in LAYER_FIXTURES
    ]


def _all_sources() -> list[ConfigSource]:
    return [
        _direct_source(
            ConfigLayer.CORE_DEFAULTS,
            "defaults-a",
            {
                "settings": {
                    "media": {
                        "duration_seconds": 25,
                        "aspect_ratio": {"width": 1, "height": 1},
                        "platforms": ["platform-base"],
                    }
                }
            },
        ),
        *_persisted_sources(),
        _direct_source(
            ConfigLayer.RUNTIME_OVERRIDE,
            "runtime-a",
            {"settings": {"media": {"duration_seconds": 85}}},
        ),
        _direct_source(
            ConfigLayer.HUMAN_DECISION,
            "decision-a",
            {"settings": {"media": {"duration_seconds": 95}}},
        ),
    ]


def _merge(
    sources: list[ConfigSource],
    *,
    created_at: str = "2026-01-02T03:04:05Z",
    validators: dict[str, object] | None = None,
):
    return DeterministicConfigMerger().merge(
        sources,
        scope=EffectiveScope(
            workspace_id=OpaqueId("workspace-a"),
            channel_profile_id=OpaqueId("channel-a"),
            concept_profile_id=OpaqueId("concept-a"),
            episode_id=OpaqueId("episode-a"),
        ),
        versions=EffectiveVersions(
            core_distribution=__version__,
            core_contract=CORE_CONTRACT_VERSION,
            rules_version="rules-a",
            policy_version="policy-a/1.0",
            config_contract=CONFIG_CONTRACT_VERSION,
        ),
        core_lock_sha256=HashDigest("a" * 64),
        created_at=created_at,
        runtime_override_allowlist={"/settings/media/duration_seconds"},
        extension_validators=(
            {"namespace-a": SyntheticExtensionValidator()} if validators is None else validators
        ),
    )


def test_every_layer_overrides_the_previous_layer_in_declared_order() -> None:
    sources = _all_sources()
    expected = (
        (ConfigLayer.CORE_DEFAULTS, 25),
        (ConfigLayer.WORKSPACE, 35),
        (ConfigLayer.CHANNEL, 45),
        (ConfigLayer.CONCEPT, 55),
        (ConfigLayer.EPISODE, 75),
        (ConfigLayer.RUNTIME_OVERRIDE, 85),
        (ConfigLayer.HUMAN_DECISION, 95),
    )
    for end, (winning_layer, duration) in enumerate(expected, start=1):
        snapshot = _merge(sources[:end])
        assert snapshot.effective["settings"]["media"]["duration_seconds"] == duration
        assert (
            snapshot.provenance["/settings/media/duration_seconds"].winning_layer
            is winning_layer
        )


def test_provenance_arrays_media_replacement_and_opaque_extensions() -> None:
    snapshot = _merge(list(reversed(_all_sources())))
    media = snapshot.effective["settings"]["media"]
    assert media == {
        "duration_seconds": 95,
        "aspect_ratio": {"width": 16, "height": 9},
        "platforms": ["platform-c"],
    }
    assert snapshot.provenance["/settings/media/aspect_ratio/width"].source_id == "episode-a"
    assert snapshot.provenance["/settings/media/platforms"].source_id == "episode-a"
    assert snapshot.provenance["/settings/execution/mode"].source_id == "workspace-a"
    assert snapshot.provenance["/settings/identity/recurring_character_ids"].source_id == "concept-a"
    assert snapshot.provenance["/extensions/namespace-a"].source_id == "channel-a"
    assert snapshot.effective["extensions"]["namespace-a"]["payload"]["owner_field"] == {
        "value": 7
    }


def test_characterless_channel_and_concept_are_valid_typed_models() -> None:
    channel = parse_config_document(
        (FIXTURES / "channel_config.json").read_bytes(), ConfigLayer.CHANNEL
    )
    concept = parse_config_document(
        (FIXTURES / "concept_config.json").read_bytes(), ConfigLayer.CONCEPT
    )
    assert isinstance(channel, ChannelConfig)
    assert isinstance(concept, ConceptConfig)
    assert channel.settings.identity is not None
    assert concept.settings.identity is not None
    assert channel.settings.identity.recurring_character_ids == ()
    assert concept.settings.identity.recurring_character_ids == ()


def test_media_values_are_data_not_package_defaults() -> None:
    workspace_snapshot = _merge(_all_sources()[:2])
    episode_snapshot = _merge(_all_sources()[:5])
    workspace_media = workspace_snapshot.effective["settings"]["media"]
    episode_media = episode_snapshot.effective["settings"]["media"]
    assert workspace_media["duration_seconds"] == 35
    assert workspace_media["aspect_ratio"] == {"width": 9, "height": 16}
    assert workspace_media["platforms"] == ["platform-a"]
    assert episode_media["duration_seconds"] == 75
    assert episode_media["aspect_ratio"] == {"width": 16, "height": 9}
    assert episode_media["platforms"] == ["platform-c"]


def test_effective_hash_is_repeatable_and_excludes_creation_time() -> None:
    first = _merge(_all_sources(), created_at="2026-01-02T03:04:05Z")
    second = _merge(_all_sources(), created_at="2026-02-03T04:05:06Z")
    assert first.effective_config_sha256 == second.effective_config_sha256


def test_key_order_number_spelling_and_document_newlines_do_not_change_hash() -> None:
    values_a = {
        "settings": {
            "media": {
                "duration_seconds": 35.0,
                "aspect_ratio": {"width": 4, "height": 3},
                "platforms": ["platform-a"],
            }
        },
        "extensions": {},
    }
    values_b = {
        "extensions": {},
        "settings": {
            "media": {
                "platforms": ["platform-a"],
                "aspect_ratio": {"height": 3, "width": 4},
                "duration_seconds": Decimal("35.000"),
            }
        },
    }
    source_a = _direct_source(ConfigLayer.WORKSPACE, "workspace-order", values_a)
    source_b = _direct_source(ConfigLayer.WORKSPACE, "workspace-order", values_b)
    assert source_a.sha256 == source_b.sha256
    assert _merge([source_a]).effective_config_sha256 == _merge([source_b]).effective_config_sha256

    document = json.loads((FIXTURES / "workspace_config.json").read_text(encoding="utf-8"))
    reversed_document = {key: document[key] for key in reversed(document)}
    pretty = (json.dumps(document, indent=2) + "\r\n").encode("utf-8")
    compact = json.dumps(reversed_document, separators=(",", ":")).encode("utf-8")
    parsed_a = config_source_from_document_bytes(
        layer=ConfigLayer.WORKSPACE, path="config/workspace.json", payload=pretty
    )
    parsed_b = config_source_from_document_bytes(
        layer=ConfigLayer.WORKSPACE, path="config/workspace.json", payload=compact
    )
    assert parsed_a.sha256 == parsed_b.sha256
    assert _merge([parsed_a]).effective_config_sha256 == _merge([parsed_b]).effective_config_sha256


def test_unknown_core_fields_unregistered_extensions_and_disallowed_overrides_fail() -> None:
    typo = _direct_source(
        ConfigLayer.WORKSPACE,
        "workspace-typo",
        {"settings": {"media": {"duration_seconds": 35, "duraton_seconds": 36}}},
    )
    with pytest.raises(ConfigMergeError, match="unknown fields"):
        _merge([typo])

    extension_source = _persisted_sources()[1]
    with pytest.raises(ConfigMergeError, match="unregistered extension namespace"):
        _merge([extension_source], validators={})

    runtime = _direct_source(
        ConfigLayer.RUNTIME_OVERRIDE,
        "runtime-mode",
        {"settings": {"execution": {"mode": "mode-b"}}},
    )
    with pytest.raises(ConfigMergeError, match="disallowed fields"):
        _merge([runtime])


def test_type_conflicts_null_deletion_and_duplicate_layers_fail() -> None:
    lower = _direct_source(
        ConfigLayer.WORKSPACE,
        "workspace-type",
        {"settings": {"media": {"duration_seconds": 35}}},
    )
    conflict = ConfigSource(
        layer=ConfigLayer.CHANNEL,
        source_id=OpaqueId("channel-type"),
        path=RelativeArtifactPath("config/channel-type.json"),
        sha256=HashDigest("b" * 64),
        values={"settings": {"media": {"duration_seconds": "long"}}},
    )
    with pytest.raises(ConfigMergeError):
        _merge([lower, conflict])

    null_value = ConfigSource(
        layer=ConfigLayer.CHANNEL,
        source_id=OpaqueId("channel-null"),
        path=RelativeArtifactPath("config/channel-null.json"),
        sha256=HashDigest("c" * 64),
        values={"settings": {"media": {"duration_seconds": None}}},
    )
    with pytest.raises(ConfigMergeError):
        _merge([lower, null_value])

    duplicate = _direct_source(
        ConfigLayer.WORKSPACE,
        "workspace-duplicate",
        {"settings": {"media": {"duration_seconds": 36}}},
    )
    with pytest.raises(ConfigMergeError, match="multiple sources"):
        _merge([lower, duplicate])


def test_duplicate_json_key_in_one_layer_fails_before_merge() -> None:
    payload = b'{"artifact_version":"workspace-config/1.0","config_contract":"1.0","config_contract":"1.0","workspace_id":"workspace-a"}'
    with pytest.raises(ValueError, match="duplicate key"):
        parse_config_document(payload, ConfigLayer.WORKSPACE)
