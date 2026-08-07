"""Negative corpus for the explicit strict JSON trust boundary."""

from __future__ import annotations

from decimal import Decimal
import math
from pathlib import Path

import pytest

from video_factory.artifacts import (
    ArtifactSchemaRegistry,
    validate_artifact_bytes,
    validate_artifact_mapping,
    validate_artifact_path,
)
from video_factory.config import (
    CanonicalizationError,
    ConfigLayer,
    ConfigValidationError,
    canonical_json_bytes,
    config_source_from_document_bytes,
)
from video_factory.json_boundary import (
    JsonErrorCode,
    JsonInputError,
    JsonLimits,
    parse_json_bytes,
    parse_json_path,
    require_json_object,
    validate_json_mapping,
)


def _assert_code(error: pytest.ExceptionInfo[JsonInputError], code: JsonErrorCode) -> None:
    assert error.value.code is code
    assert str(error.value).startswith(code.value)


def test_nested_duplicate_keys_are_rejected() -> None:
    with pytest.raises(JsonInputError) as caught:
        parse_json_bytes(b'{"outer":{"same":1,"same":2}}')
    _assert_code(caught, JsonErrorCode.DUPLICATE_KEY)


@pytest.mark.parametrize("token", [b"NaN", b"Infinity", b"-Infinity"])
def test_nonfinite_json_tokens_are_rejected(token: bytes) -> None:
    with pytest.raises(JsonInputError) as caught:
        parse_json_bytes(b'{"value":' + token + b"}")
    _assert_code(caught, JsonErrorCode.NONFINITE_NUMBER)


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf"), Decimal("NaN")],
)
def test_nonfinite_mapping_numbers_are_rejected(value: object) -> None:
    with pytest.raises(JsonInputError) as caught:
        validate_json_mapping({"value": value})
    _assert_code(caught, JsonErrorCode.NONFINITE_NUMBER)


@pytest.mark.parametrize("payload", [b"\xff", b"\xef\xbb\xbf{}"])
def test_invalid_utf8_or_bom_is_rejected(payload: bytes) -> None:
    with pytest.raises(JsonInputError) as caught:
        parse_json_bytes(payload)
    assert caught.value.code in {
        JsonErrorCode.INVALID_UTF8,
        JsonErrorCode.INVALID_SYNTAX,
    }


def test_byte_size_depth_and_node_limits_are_enforced() -> None:
    with pytest.raises(JsonInputError) as too_large:
        parse_json_bytes(b'{"long":"value"}', limits=JsonLimits(max_bytes=4))
    _assert_code(too_large, JsonErrorCode.INPUT_TOO_LARGE)

    with pytest.raises(JsonInputError) as too_deep:
        parse_json_bytes(
            b'{"a":{"b":{"c":1}}}',
            limits=JsonLimits(max_depth=2),
        )
    _assert_code(too_deep, JsonErrorCode.MAX_DEPTH_EXCEEDED)

    with pytest.raises(JsonInputError) as too_many:
        parse_json_bytes(
            b'{"a":1,"b":2,"c":3}',
            limits=JsonLimits(max_nodes=3),
        )
    _assert_code(too_many, JsonErrorCode.MAX_NODES_EXCEEDED)


@pytest.mark.parametrize("token", [b"1e400", b"1e-400", b"123456789"])
def test_numeric_range_and_token_limits_are_enforced(token: bytes) -> None:
    with pytest.raises(JsonInputError) as caught:
        parse_json_bytes(
            b'{"value":' + token + b"}",
            limits=JsonLimits(max_number_chars=8),
        )
    _assert_code(caught, JsonErrorCode.NUMBER_OUT_OF_RANGE)


@pytest.mark.parametrize("token", [b"1e1000000", b"1e-1000000"])
def test_decimal_fixed_point_expansion_is_rejected_from_bytes(token: bytes) -> None:
    with pytest.raises(JsonInputError) as caught:
        parse_json_bytes(b'{"value":' + token + b"}", decimal_numbers=True)
    _assert_code(caught, JsonErrorCode.NUMBER_OUT_OF_RANGE)


@pytest.mark.parametrize("value", [Decimal("1e1000000"), Decimal("1e-1000000")])
def test_decimal_fixed_point_expansion_is_rejected_from_mapping(
    value: Decimal,
) -> None:
    with pytest.raises(JsonInputError) as caught:
        validate_json_mapping({"value": value})
    _assert_code(caught, JsonErrorCode.NUMBER_OUT_OF_RANGE)


def test_runtime_integer_conversion_limit_is_a_stable_domain_error() -> None:
    with pytest.raises(JsonInputError) as caught:
        parse_json_bytes(
            b'{"value":' + (b"9" * 5_000) + b"}",
            limits=JsonLimits(max_bytes=8_000, max_number_chars=6_000),
        )
    _assert_code(caught, JsonErrorCode.NUMBER_OUT_OF_RANGE)


def test_decimal_runtime_exponent_limit_is_a_stable_domain_error() -> None:
    payload = b'{"value":1e' + (b"9" * 1_000) + b"}"
    with pytest.raises(JsonInputError) as caught:
        parse_json_bytes(payload, decimal_numbers=True)
    _assert_code(caught, JsonErrorCode.NUMBER_OUT_OF_RANGE)


@pytest.mark.parametrize("token", [b"1e1000000", b"1e-1000000"])
def test_config_source_rejects_decimal_expansion_before_canonicalization(
    token: bytes,
) -> None:
    payload = (
        b'{"artifact_version":"workspace-config/1.0",'
        b'"config_contract":"1.0","workspace_id":"workspace-a",'
        b'"settings":{},"extensions":{"extension-a":{'
        b'"contract_version":"1.0","payload":{"value":'
        + token
        + b"}}}}"
    )
    with pytest.raises(ConfigValidationError, match="json.number_out_of_range"):
        config_source_from_document_bytes(
            layer=ConfigLayer.WORKSPACE,
            path="config/workspace.json",
            payload=payload,
        )


@pytest.mark.parametrize("value", [Decimal("1e1000000"), Decimal("1e-1000000")])
def test_canonical_serializer_has_an_independent_decimal_expansion_guard(
    value: Decimal,
) -> None:
    with pytest.raises(CanonicalizationError, match="safety limit"):
        canonical_json_bytes({"value": value})


def test_mapping_cycles_keys_surrogates_and_custom_values_are_rejected() -> None:
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    with pytest.raises(JsonInputError) as cycle:
        validate_json_mapping(cyclic)
    _assert_code(cycle, JsonErrorCode.CYCLIC_VALUE)

    with pytest.raises(JsonInputError) as bad_key:
        validate_json_mapping({1: "value"})  # type: ignore[dict-item]
    _assert_code(bad_key, JsonErrorCode.UNSUPPORTED_VALUE)

    with pytest.raises(JsonInputError) as surrogate:
        validate_json_mapping({"value": "\ud800"})
    _assert_code(surrogate, JsonErrorCode.UNSUPPORTED_VALUE)

    with pytest.raises(JsonInputError) as custom:
        validate_json_mapping({"value": complex(1, 2)})
    _assert_code(custom, JsonErrorCode.UNSUPPORTED_VALUE)


def test_explicit_path_errors_are_stable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(JsonInputError) as absent:
        parse_json_path(missing)
    _assert_code(absent, JsonErrorCode.PATH_NOT_FOUND)

    with pytest.raises(JsonInputError) as directory:
        parse_json_path(tmp_path)
    _assert_code(directory, JsonErrorCode.PATH_NOT_FILE)

    source = tmp_path / "locked.json"
    source.write_bytes(b"{}")
    original_open = Path.open

    def denied(self: Path, *args: object, **kwargs: object):
        if self == source:
            raise PermissionError("synthetic denial")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied)
    with pytest.raises(JsonInputError) as unreadable:
        parse_json_path(source)
    _assert_code(unreadable, JsonErrorCode.PATH_UNREADABLE)


def test_artifact_entry_points_report_structured_boundary_failures(tmp_path: Path) -> None:
    missing = validate_artifact_path(tmp_path / "missing.json")
    assert missing.ok is False
    assert missing.errors[0].reason_code == JsonErrorCode.PATH_NOT_FOUND.value

    duplicate = validate_artifact_bytes(
        b'{"artifact_version":"x/1.0","artifact_version":"x/1.0"}'
    )
    assert duplicate.ok is False
    assert duplicate.errors[0].reason_code == JsonErrorCode.DUPLICATE_KEY.value

    nonfinite = validate_artifact_mapping(
        {"artifact_version": "x/1.0", "value": math.nan}
    )
    assert nonfinite.ok is False
    assert nonfinite.errors[0].reason_code == JsonErrorCode.NONFINITE_NUMBER.value


def test_stdlib_rfc3339_checker_is_active_without_optional_dependency(
    tmp_path: Path,
) -> None:
    schema = tmp_path / "dated.schema.json"
    schema.write_text(
        """{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://schemas.invalid/dated.schema.json",
  "type": "object",
  "required": ["artifact_version", "occurred_at"],
  "properties": {
    "artifact_version": {"const": "dated/1.0"},
    "occurred_at": {"type": "string", "format": "date-time"}
  },
  "additionalProperties": false
}\n""",
        encoding="utf-8",
        newline="\n",
    )
    registry = ArtifactSchemaRegistry(tmp_path)
    valid = validate_artifact_mapping(
        {"artifact_version": "dated/1.0", "occurred_at": "2026-08-07T12:30:00+09:00"},
        registry=registry,
    )
    assert valid.ok is True

    for invalid in (
        "2026-08-07T12:30:00",
        "2026-02-30T00:00:00Z",
        "2026-08-07T24:00:00Z",
        "2026-08-07T12:30:00+24:00",
    ):
        result = validate_artifact_mapping(
            {"artifact_version": "dated/1.0", "occurred_at": invalid},
            registry=registry,
        )
        assert result.ok is False
        assert any(error.validator == "format" for error in result.errors)
