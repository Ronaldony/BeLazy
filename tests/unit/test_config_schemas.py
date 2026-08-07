from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

from video_factory import CORE_CONTRACT_VERSION, __version__
from video_factory.config import (
    CONFIG_CONTRACT_VERSION,
    ConfigLayer,
    ConfigSource,
    DeterministicConfigMerger,
    EffectiveScope,
    EffectiveVersions,
    canonical_sha256,
    snapshot_to_mapping,
)
from video_factory.domain import HashDigest, OpaqueId, RelativeArtifactPath


ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "schemas"
FIXTURES = ROOT / "tests" / "fixtures"


def _schemas() -> dict[str, dict[str, object]]:
    return {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in SCHEMAS.glob("*.schema.json")
    }


def _validator(filename: str) -> Draft202012Validator:
    schemas = _schemas()
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas.values()
    )
    return Draft202012Validator(schemas[filename], registry=registry)


@pytest.mark.parametrize(
    ("schema_name", "fixture_name"),
    (
        ("workspace-config.schema.json", "workspace_config.json"),
        ("channel-config.schema.json", "channel_config.json"),
        ("concept-config.schema.json", "concept_config.json"),
        ("episode-config.schema.json", "episode_config.json"),
    ),
)
def test_all_four_synthetic_layer_documents_match_their_schemas(
    schema_name: str,
    fixture_name: str,
) -> None:
    instance = json.loads((FIXTURES / fixture_name).read_text(encoding="utf-8"))
    _validator(schema_name).validate(instance)


@pytest.mark.parametrize(
    "mutator",
    (
        lambda value: value.update({"unexpected": 1}),
        lambda value: value["settings"]["media"].update({"duration_seconds": 0}),
        lambda value: value.update({"config_contract": "2.0"}),
        lambda value: value["settings"]["media"].update({"aspect_ratio": {"width": 0, "height": 1}}),
    ),
)
def test_schema_rejects_invalid_or_unknown_core_configuration(mutator) -> None:
    instance = json.loads((FIXTURES / "workspace_config.json").read_text(encoding="utf-8"))
    mutator(instance)
    with pytest.raises(ValidationError):
        _validator("workspace-config.schema.json").validate(instance)


def test_extension_payload_is_open_but_its_core_owned_wrapper_is_closed() -> None:
    instance = json.loads((FIXTURES / "channel_config.json").read_text(encoding="utf-8"))
    instance["extensions"]["namespace-a"]["payload"]["future_owner_field"] = [1, 2, 3]
    _validator("channel-config.schema.json").validate(instance)

    invalid = deepcopy(instance)
    invalid["extensions"]["namespace-a"]["wrapper_typo"] = 1
    with pytest.raises(ValidationError):
        _validator("channel-config.schema.json").validate(invalid)


def test_generated_effective_snapshot_matches_closed_schema() -> None:
    values = {
        "settings": {
            "media": {
                "duration_seconds": 35,
                "aspect_ratio": {"width": 4, "height": 3},
                "platforms": ["platform-a"],
            },
            "identity": {"recurring_character_ids": []},
            "execution": {"mode": "mode-a"},
        },
        "extensions": {},
    }
    source = ConfigSource(
        layer=ConfigLayer.WORKSPACE,
        source_id=OpaqueId("workspace-schema"),
        path=RelativeArtifactPath("config/workspace-schema.json"),
        sha256=canonical_sha256(values),
        values=values,
    )
    snapshot = DeterministicConfigMerger().merge(
        [source],
        scope=EffectiveScope(
            workspace_id=OpaqueId("workspace-schema"),
            channel_profile_id=OpaqueId("channel-schema"),
            concept_profile_id=None,
            episode_id=None,
        ),
        versions=EffectiveVersions(
            core_distribution=__version__,
            core_contract=CORE_CONTRACT_VERSION,
            rules_version="rules-schema",
            policy_version="policy-schema/1.0",
            config_contract=CONFIG_CONTRACT_VERSION,
        ),
        core_lock_sha256=HashDigest("d" * 64),
        created_at="2026-01-02T03:04:05Z",
        runtime_override_allowlist=set(),
        extension_validators={},
    )
    instance = snapshot_to_mapping(snapshot)
    _validator("effective-config.schema.json").validate(instance)

    instance["unknown"] = True
    with pytest.raises(ValidationError):
        _validator("effective-config.schema.json").validate(instance)
