"""Strict, bounded JSON ingestion for untrusted production inputs.

The public entry points are deliberately unambiguous: bytes are bytes, paths
are paths, and already-decoded mappings are mappings.  Duplicate-key defense
is necessarily available only while parsing bytes/path content because an
ordinary ``dict`` has already discarded that information.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import json
import math
from pathlib import Path
import re
from typing import Any


type JsonValue = (
    None
    | bool
    | int
    | float
    | str
    | list["JsonValue"]
    | dict[str, "JsonValue"]
)


class JsonErrorCode(StrEnum):
    PATH_NOT_FOUND = "json.path_not_found"
    PATH_NOT_FILE = "json.path_not_file"
    PATH_UNREADABLE = "json.path_unreadable"
    INPUT_TOO_LARGE = "json.input_too_large"
    INVALID_UTF8 = "json.invalid_utf8"
    INVALID_SYNTAX = "json.invalid_syntax"
    DUPLICATE_KEY = "json.duplicate_key"
    NONFINITE_NUMBER = "json.nonfinite_number"
    NUMBER_OUT_OF_RANGE = "json.number_out_of_range"
    MAX_DEPTH_EXCEEDED = "json.max_depth_exceeded"
    MAX_NODES_EXCEEDED = "json.max_nodes_exceeded"
    TOP_LEVEL_NOT_OBJECT = "json.top_level_not_object"
    UNSUPPORTED_VALUE = "json.unsupported_value"
    CYCLIC_VALUE = "json.cyclic_value"


@dataclass(frozen=True, slots=True)
class JsonLimits:
    max_bytes: int = 8 * 1024 * 1024
    max_depth: int = 64
    max_nodes: int = 100_000
    max_number_chars: int = 1_024

    def __post_init__(self) -> None:
        if min(
            self.max_bytes,
            self.max_depth,
            self.max_nodes,
            self.max_number_chars,
        ) < 1:
            raise ValueError("all JSON limits must be positive")


DEFAULT_JSON_LIMITS = JsonLimits()


class JsonInputError(ValueError):
    """Typed boundary failure with a stable machine-readable reason code."""

    def __init__(
        self,
        code: JsonErrorCode,
        detail: str,
        *,
        source: str | None = None,
        path: str = "$",
    ) -> None:
        self.code = code
        self.detail = detail
        self.source = source
        self.path = path
        suffix = f" ({source})" if source is not None else ""
        super().__init__(f"{code.value}: {detail}{suffix}")


class _DuplicateKey(ValueError):
    pass


class _NonfiniteNumber(ValueError):
    pass


class _NumberOutOfRange(ValueError):
    pass


def _contains_lone_surrogate(value: str) -> bool:
    return any(0xD800 <= ord(character) <= 0xDFFF for character in value)


def _bounded_int(token: str, limits: JsonLimits) -> int:
    if len(token.lstrip("-")) > limits.max_number_chars:
        raise _NumberOutOfRange("integer token exceeds max_number_chars")
    try:
        return int(token)
    except ValueError as error:
        raise _NumberOutOfRange(
            "integer exceeds the runtime conversion safety limit"
        ) from error


def _bounded_float(token: str, limits: JsonLimits) -> float:
    if len(token) > limits.max_number_chars:
        raise _NumberOutOfRange("number token exceeds max_number_chars")
    value = float(token)
    if not math.isfinite(value):
        raise _NumberOutOfRange("number overflows the finite float range")
    if value == 0.0:
        significand = token.lower().split("e", 1)[0]
        if any(character in "123456789" for character in significand):
            raise _NumberOutOfRange("non-zero number underflows the finite float range")
    return value


def _bounded_decimal(token: str, limits: JsonLimits) -> Decimal:
    if len(token) > limits.max_number_chars:
        raise _NumberOutOfRange("number token exceeds max_number_chars")
    try:
        value = Decimal(token)
    except (InvalidOperation, ValueError) as error:
        raise _NumberOutOfRange(
            "number exponent exceeds the Decimal runtime range"
        ) from error
    if not value.is_finite():
        raise _NonfiniteNumber("non-finite Decimal is forbidden")
    if decimal_fixed_point_length(value) > limits.max_number_chars:
        raise _NumberOutOfRange(
            "number fixed-point representation exceeds max_number_chars"
        )
    return value


def decimal_fixed_point_length(value: Decimal) -> int:
    """Return the allocation-free upper bound for ``format(value, 'f')``.

    Scientific notation can be tiny while its fixed-point representation is
    enormous.  Computing the length from ``Decimal.as_tuple()`` prevents an
    attacker-controlled exponent from triggering that allocation.
    """

    if not value.is_finite():
        raise ValueError("non-finite Decimal has no bounded fixed-point form")
    if value.is_zero():
        return 1
    parts = value.as_tuple()
    exponent = parts.exponent
    if not isinstance(exponent, int):  # pragma: no cover - finite invariant
        raise ValueError("finite Decimal exponent must be an integer")
    digit_count = len(parts.digits)
    decimal_point = digit_count + exponent
    sign_chars = 1 if parts.sign else 0
    if exponent >= 0:
        body_chars = digit_count + exponent
    elif decimal_point > 0:
        body_chars = digit_count + 1
    else:
        body_chars = 2 + (-decimal_point) + digit_count
    return sign_chars + body_chars


def _reject_constant(token: str) -> float:
    raise _NonfiniteNumber(f"non-finite JSON number {token!r} is forbidden")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(f"duplicate key {key!r} in one object")
        result[key] = value
    return result


def _validate_value(
    value: object,
    *,
    limits: JsonLimits,
    source: str | None,
) -> JsonValue:
    nodes = 0
    active: set[int] = set()

    def visit(item: object, depth: int, path: str) -> JsonValue:
        nonlocal nodes
        nodes += 1
        if nodes > limits.max_nodes:
            raise JsonInputError(
                JsonErrorCode.MAX_NODES_EXCEEDED,
                f"JSON value exceeds {limits.max_nodes} nodes",
                source=source,
                path=path,
            )
        if depth > limits.max_depth:
            raise JsonInputError(
                JsonErrorCode.MAX_DEPTH_EXCEEDED,
                f"JSON value exceeds depth {limits.max_depth}",
                source=source,
                path=path,
            )

        if item is None or isinstance(item, bool):
            return item
        if isinstance(item, str):
            if _contains_lone_surrogate(item):
                raise JsonInputError(
                    JsonErrorCode.UNSUPPORTED_VALUE,
                    "strings must contain Unicode scalar values, not lone surrogates",
                    source=source,
                    path=path,
                )
            return item
        if isinstance(item, int):
            try:
                digits = len(str(abs(item)))
            except ValueError as error:
                raise JsonInputError(
                    JsonErrorCode.NUMBER_OUT_OF_RANGE,
                    "integer cannot be represented within the configured limit",
                    source=source,
                    path=path,
                ) from error
            if digits > limits.max_number_chars:
                raise JsonInputError(
                    JsonErrorCode.NUMBER_OUT_OF_RANGE,
                    "integer exceeds max_number_chars",
                    source=source,
                    path=path,
                )
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                raise JsonInputError(
                    JsonErrorCode.NONFINITE_NUMBER,
                    "mapping contains a non-finite float",
                    source=source,
                    path=path,
                )
            return item
        if isinstance(item, Decimal):
            if not item.is_finite():
                raise JsonInputError(
                    JsonErrorCode.NONFINITE_NUMBER,
                    "mapping contains a non-finite Decimal",
                    source=source,
                    path=path,
                )
            if (
                len(str(item)) > limits.max_number_chars
                or decimal_fixed_point_length(item) > limits.max_number_chars
            ):
                raise JsonInputError(
                    JsonErrorCode.NUMBER_OUT_OF_RANGE,
                    "Decimal fixed-point representation exceeds max_number_chars",
                    source=source,
                    path=path,
                )
            return item  # type: ignore[return-value]

        if isinstance(item, Mapping):
            identity = id(item)
            if identity in active:
                raise JsonInputError(
                    JsonErrorCode.CYCLIC_VALUE,
                    "mapping contains a cycle",
                    source=source,
                    path=path,
                )
            active.add(identity)
            try:
                output: dict[str, JsonValue] = {}
                for key, child in item.items():
                    if not isinstance(key, str) or _contains_lone_surrogate(key):
                        raise JsonInputError(
                            JsonErrorCode.UNSUPPORTED_VALUE,
                            "JSON object keys must be Unicode scalar strings",
                            source=source,
                            path=path,
                        )
                    child_path = f"{path}.{key}"
                    output[key] = visit(child, depth + 1, child_path)
                return output
            finally:
                active.remove(identity)

        if isinstance(item, list):
            identity = id(item)
            if identity in active:
                raise JsonInputError(
                    JsonErrorCode.CYCLIC_VALUE,
                    "array contains a cycle",
                    source=source,
                    path=path,
                )
            active.add(identity)
            try:
                return [
                    visit(child, depth + 1, f"{path}[{index}]")
                    for index, child in enumerate(item)
                ]
            finally:
                active.remove(identity)

        raise JsonInputError(
            JsonErrorCode.UNSUPPORTED_VALUE,
            f"unsupported JSON value type: {type(item).__name__}",
            source=source,
            path=path,
        )

    return visit(value, 0, "$")


def parse_json_bytes(
    payload: bytes,
    *,
    limits: JsonLimits = DEFAULT_JSON_LIMITS,
    source: str | None = None,
    decimal_numbers: bool = False,
) -> JsonValue:
    """Parse exact UTF-8 JSON bytes with duplicate, number and resource checks."""

    if not isinstance(payload, bytes):
        raise JsonInputError(
            JsonErrorCode.UNSUPPORTED_VALUE,
            "payload must be bytes",
            source=source,
        )
    if len(payload) > limits.max_bytes:
        raise JsonInputError(
            JsonErrorCode.INPUT_TOO_LARGE,
            f"input exceeds {limits.max_bytes} bytes",
            source=source,
        )
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise JsonInputError(
            JsonErrorCode.INVALID_UTF8,
            "input is not valid UTF-8",
            source=source,
        ) from error
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_int=lambda token: _bounded_int(token, limits),
            parse_float=(
                (lambda token: _bounded_decimal(token, limits))
                if decimal_numbers
                else (lambda token: _bounded_float(token, limits))
            ),
            parse_constant=_reject_constant,
        )
    except _DuplicateKey as error:
        raise JsonInputError(
            JsonErrorCode.DUPLICATE_KEY,
            str(error),
            source=source,
        ) from error
    except _NonfiniteNumber as error:
        raise JsonInputError(
            JsonErrorCode.NONFINITE_NUMBER,
            str(error),
            source=source,
        ) from error
    except _NumberOutOfRange as error:
        raise JsonInputError(
            JsonErrorCode.NUMBER_OUT_OF_RANGE,
            str(error),
            source=source,
        ) from error
    except (json.JSONDecodeError, RecursionError) as error:
        raise JsonInputError(
            JsonErrorCode.INVALID_SYNTAX,
            f"invalid JSON syntax: {error}",
            source=source,
        ) from error
    return _validate_value(parsed, limits=limits, source=source)


def parse_json_path(
    path: str | Path,
    *,
    limits: JsonLimits = DEFAULT_JSON_LIMITS,
    decimal_numbers: bool = False,
) -> JsonValue:
    """Read and parse one explicit path using a bounded binary read."""

    if not isinstance(path, (str, Path)):
        raise JsonInputError(
            JsonErrorCode.UNSUPPORTED_VALUE,
            "path must be str or pathlib.Path",
        )
    source = Path(path)
    label = str(source)
    if not source.exists():
        raise JsonInputError(
            JsonErrorCode.PATH_NOT_FOUND,
            "JSON path does not exist",
            source=label,
        )
    if not source.is_file():
        raise JsonInputError(
            JsonErrorCode.PATH_NOT_FILE,
            "JSON path is not a regular file",
            source=label,
        )
    try:
        with source.open("rb") as stream:
            payload = stream.read(limits.max_bytes + 1)
    except OSError as error:
        raise JsonInputError(
            JsonErrorCode.PATH_UNREADABLE,
            f"cannot read JSON path: {error}",
            source=label,
        ) from error
    if len(payload) > limits.max_bytes:
        raise JsonInputError(
            JsonErrorCode.INPUT_TOO_LARGE,
            f"input exceeds {limits.max_bytes} bytes",
            source=label,
        )
    return parse_json_bytes(
        payload,
        limits=limits,
        source=label,
        decimal_numbers=decimal_numbers,
    )


def validate_json_mapping(
    value: Mapping[str, object],
    *,
    limits: JsonLimits = DEFAULT_JSON_LIMITS,
    source: str | None = None,
) -> dict[str, JsonValue]:
    """Validate and normalize an already-decoded JSON object mapping."""

    if not isinstance(value, Mapping):
        raise JsonInputError(
            JsonErrorCode.TOP_LEVEL_NOT_OBJECT,
            "top-level JSON value must be an object",
            source=source,
        )
    normalized = _validate_value(value, limits=limits, source=source)
    assert isinstance(normalized, dict)
    return normalized


def require_json_object(
    value: JsonValue,
    *,
    source: str | None = None,
) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise JsonInputError(
            JsonErrorCode.TOP_LEVEL_NOT_OBJECT,
            "top-level JSON value must be an object",
            source=source,
        )
    return value


_RFC3339 = re.compile(
    r"^(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})"
    r"[Tt](?P<time>[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?)"
    r"(?P<zone>[Zz]|[+-][0-9]{2}:[0-9]{2})$"
)


def parse_rfc3339_datetime(value: str) -> datetime:
    """Parse an RFC 3339 date-time using only the Python standard library."""

    if not isinstance(value, str):
        raise ValueError("date-time must be a string")
    match = _RFC3339.fullmatch(value)
    if match is None:
        raise ValueError("date-time must include a full date, time and UTC offset")
    zone = match.group("zone")
    normalized_zone = "+00:00" if zone in {"Z", "z"} else zone
    try:
        parsed = datetime.fromisoformat(
            f"{match.group('date')}T{match.group('time')}{normalized_zone}"
        )
    except ValueError as error:
        raise ValueError("date-time contains an impossible calendar or clock value") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("date-time must be timezone-aware")
    return parsed


def is_rfc3339_datetime(value: object) -> bool:
    if not isinstance(value, str):
        return True
    try:
        parse_rfc3339_datetime(value)
    except ValueError:
        return False
    return True


__all__ = [
    "DEFAULT_JSON_LIMITS",
    "JsonErrorCode",
    "JsonInputError",
    "JsonLimits",
    "JsonValue",
    "is_rfc3339_datetime",
    "parse_json_bytes",
    "parse_json_path",
    "parse_rfc3339_datetime",
    "require_json_object",
    "validate_json_mapping",
]
