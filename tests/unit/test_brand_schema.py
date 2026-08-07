from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from video_factory.brand import (
    BrandEntityValidationError,
    EntityKind,
    LocationData,
    entity_from_mapping,
    entity_to_mapping,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
SCHEMA = ROOT / "schemas" / "brand-entity.schema.json"


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "fixture_name",
    ("brand_character_shadow.json", "brand_location_shadow.json"),
)
def test_character_and_location_use_the_same_closed_schema(fixture_name: str) -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(_fixture(fixture_name))


def test_location_kind_uses_its_typed_extension_without_character_fields() -> None:
    entity = entity_from_mapping(_fixture("brand_location_shadow.json"))

    assert entity.entity_kind is EntityKind.LOCATION
    assert isinstance(entity.data, LocationData)
    assert entity.data.reference_tokens == (
        "places/assets/place-a-wide.png",
        "place-a-detail.png",
    )
    assert "character" not in entity_to_mapping(entity)["data"]


def test_schema_and_runtime_reject_unknown_fields_and_false_fixed_hashes() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    unknown = _fixture("brand_character_shadow.json")
    unknown["data"]["character"]["undeclared"] = True
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(unknown)

    false_hash = deepcopy(_fixture("brand_character_shadow.json"))
    false_hash["fixed_sentences"][0]["sha256"] = "0" * 64
    with pytest.raises(BrandEntityValidationError, match="does not match"):
        entity_from_mapping(false_hash)
