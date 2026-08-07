"""Canonical JSON bytes used by configuration and source digests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
import hashlib
import json
import math

from video_factory.domain import HashDigest
from video_factory.json_boundary import decimal_fixed_point_length


class CanonicalizationError(ValueError):
    """Raised when a value cannot be represented by the canonical JSON contract."""


MAX_CANONICAL_NUMBER_CHARS = 1_024


def _number_text(value: int | float | Decimal) -> str:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalizationError("non-finite numbers are not valid canonical JSON")
        decimal_value = Decimal(str(value))
    elif isinstance(value, Decimal):
        if not value.is_finite():
            raise CanonicalizationError("non-finite numbers are not valid canonical JSON")
        decimal_value = value
    else:
        return str(value)

    if decimal_value.is_zero():
        return "0"
    if decimal_fixed_point_length(decimal_value) > MAX_CANONICAL_NUMBER_CHARS:
        raise CanonicalizationError(
            "number fixed-point representation exceeds canonical safety limit"
        )
    rendered = format(decimal_value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _serialize(value: object) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, (int, float, Decimal)):
        return _number_text(value)
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise CanonicalizationError("canonical JSON object keys must be strings")
        members = (
            f"{_serialize(key)}:{_serialize(value[key])}"
            for key in sorted(value)
        )
        return "{" + ",".join(members) + "}"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "[" + ",".join(_serialize(item) for item in value) + "]"
    raise CanonicalizationError(
        f"unsupported canonical JSON value type: {type(value).__name__}"
    )


def canonical_json_bytes(value: object) -> bytes:
    """Serialize without insignificant whitespace, a BOM, or a trailing newline."""

    return _serialize(value).encode("utf-8")


def canonical_sha256(value: object) -> HashDigest:
    return HashDigest(hashlib.sha256(canonical_json_bytes(value)).hexdigest())
