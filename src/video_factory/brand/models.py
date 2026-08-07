"""Closed, channel-neutral models for projected identity and place data."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
import hashlib
from pathlib import PurePosixPath
import re
from typing import TypeAlias

from video_factory.domain import ArtifactVersion, HashDigest, OpaqueId, RelativeArtifactPath


BRAND_ENTITY_ARTIFACT_VERSION = ArtifactVersion("brand-entity/1.0")

_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


class BrandEntityValidationError(ValueError):
    """Raised when a projected entity violates the closed contract."""


class EntityKind(str, Enum):
    CHARACTER = "character"
    LOCATION = "location"


class CanonicalStatus(str, Enum):
    SHADOW = "shadow"
    CANONICAL = "canonical"


@dataclass(frozen=True, slots=True)
class SourceSectionRange:
    """Exact heading-bounded source region used by an owner-side extractor."""

    start_heading: str
    end_before_heading: str | None


@dataclass(frozen=True, slots=True)
class EntitySource:
    """Byte identity and declared extraction bounds for one Markdown source."""

    path: RelativeArtifactPath
    sha256: HashDigest
    section_ranges: tuple[SourceSectionRange, ...]


@dataclass(frozen=True, slots=True)
class FixedSentence:
    """One indivisible sentence block with its role and consumers."""

    role: OpaqueId
    consumers: tuple[OpaqueId, ...]
    text: str
    source_section: str
    sha256: HashDigest


@dataclass(frozen=True, slots=True)
class CharacterData:
    """Character-specific extension of the common entity envelope."""

    display_name: str
    design_status: str
    visual_reference_path: RelativeArtifactPath
    scale_anchor: str
    invariants: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LocationData:
    """Location-specific extension of the common entity envelope."""

    display_name: str
    reference_tokens: tuple[str, ...]
    usage: str
    notes: tuple[str, ...]


BrandData: TypeAlias = CharacterData | LocationData


@dataclass(frozen=True, slots=True)
class BrandEntity:
    """Versioned entity projection without channel-specific values or selection policy."""

    artifact_version: ArtifactVersion
    entity_id: OpaqueId
    entity_kind: EntityKind
    canonical_status: CanonicalStatus
    source: EntitySource
    rules_version: str
    fixed_sentences: tuple[FixedSentence, ...]
    data: BrandData


def _fail(message: str) -> BrandEntityValidationError:
    return BrandEntityValidationError(message)


def _mapping(value: object, pointer: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise _fail(f"{pointer} must be an object with string keys")
    return value


def _sequence(value: object, pointer: str, *, allow_empty: bool = False) -> Sequence[object]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise _fail(f"{pointer} must be an array")
    if not value and not allow_empty:
        raise _fail(f"{pointer} must not be empty")
    return value


def _reject_unknown(value: Mapping[str, object], allowed: set[str], pointer: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise _fail(f"{pointer} contains unknown fields: {', '.join(unknown)}")


def _require(value: Mapping[str, object], required: set[str], pointer: str) -> None:
    missing = sorted(required - set(value))
    if missing:
        raise _fail(f"{pointer} is missing required fields: {', '.join(missing)}")


def _text(value: object, pointer: str) -> str:
    if not isinstance(value, str) or not value:
        raise _fail(f"{pointer} must be a non-empty string")
    return value


def _opaque_id(value: object, pointer: str) -> OpaqueId:
    text = _text(value, pointer)
    if _OPAQUE_ID.fullmatch(text) is None:
        raise _fail(f"{pointer} must be an opaque identifier")
    return OpaqueId(text)


def _digest(value: object, pointer: str) -> HashDigest:
    text = _text(value, pointer)
    if _SHA256.fullmatch(text) is None:
        raise _fail(f"{pointer} must be a lowercase SHA-256 digest")
    return HashDigest(text)


def _relative_path(value: object, pointer: str) -> RelativeArtifactPath:
    text = _text(value, pointer)
    path = PurePosixPath(text)
    if (
        "\\" in text
        or path.is_absolute()
        or _WINDOWS_DRIVE.match(text) is not None
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != text
    ):
        raise _fail(f"{pointer} must be a normalized relative POSIX path")
    return RelativeArtifactPath(text)


def _strings(value: object, pointer: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    values = tuple(
        _text(item, f"{pointer}/{index}")
        for index, item in enumerate(_sequence(value, pointer, allow_empty=allow_empty))
    )
    if len(set(values)) != len(values):
        raise _fail(f"{pointer} must contain unique values")
    return values


def _identifiers(value: object, pointer: str) -> tuple[OpaqueId, ...]:
    values = tuple(
        _opaque_id(item, f"{pointer}/{index}")
        for index, item in enumerate(_sequence(value, pointer))
    )
    if len(set(values)) != len(values):
        raise _fail(f"{pointer} must contain unique values")
    return values


def _source(value: object) -> EntitySource:
    source = _mapping(value, "/source")
    allowed = {"path", "sha256", "section_ranges"}
    _reject_unknown(source, allowed, "/source")
    _require(source, allowed, "/source")
    ranges: list[SourceSectionRange] = []
    for index, raw_range in enumerate(_sequence(source["section_ranges"], "/source/section_ranges")):
        pointer = f"/source/section_ranges/{index}"
        section_range = _mapping(raw_range, pointer)
        _reject_unknown(section_range, {"start_heading", "end_before_heading"}, pointer)
        _require(section_range, {"start_heading", "end_before_heading"}, pointer)
        end = section_range["end_before_heading"]
        if end is not None and (not isinstance(end, str) or not end):
            raise _fail(f"{pointer}/end_before_heading must be null or a non-empty string")
        ranges.append(
            SourceSectionRange(
                start_heading=_text(section_range["start_heading"], f"{pointer}/start_heading"),
                end_before_heading=end,
            )
        )
    starts = [item.start_heading for item in ranges]
    if len(set(starts)) != len(starts):
        raise _fail("/source/section_ranges start headings must be unique")
    return EntitySource(
        path=_relative_path(source["path"], "/source/path"),
        sha256=_digest(source["sha256"], "/source/sha256"),
        section_ranges=tuple(ranges),
    )


def _fixed_sentences(value: object) -> tuple[FixedSentence, ...]:
    result: list[FixedSentence] = []
    for index, raw_sentence in enumerate(_sequence(value, "/fixed_sentences")):
        pointer = f"/fixed_sentences/{index}"
        sentence = _mapping(raw_sentence, pointer)
        allowed = {"role", "consumers", "text", "source_section", "sha256"}
        _reject_unknown(sentence, allowed, pointer)
        _require(sentence, allowed, pointer)
        text = _text(sentence["text"], f"{pointer}/text")
        digest = _digest(sentence["sha256"], f"{pointer}/sha256")
        actual = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest != actual:
            raise _fail(f"{pointer}/sha256 does not match UTF-8 text bytes")
        result.append(
            FixedSentence(
                role=_opaque_id(sentence["role"], f"{pointer}/role"),
                consumers=_identifiers(sentence["consumers"], f"{pointer}/consumers"),
                text=text,
                source_section=_text(sentence["source_section"], f"{pointer}/source_section"),
                sha256=digest,
            )
        )
    roles = [item.role for item in result]
    if len(set(roles)) != len(roles):
        raise _fail("/fixed_sentences roles must be unique; entries cannot be merged")
    return tuple(result)


def _character_data(value: Mapping[str, object]) -> CharacterData:
    _reject_unknown(value, {"display_name", "character"}, "/data")
    _require(value, {"display_name", "character"}, "/data")
    extension = _mapping(value["character"], "/data/character")
    allowed = {"design_status", "visual_reference_path", "scale_anchor", "invariants"}
    _reject_unknown(extension, allowed, "/data/character")
    _require(extension, allowed, "/data/character")
    return CharacterData(
        display_name=_text(value["display_name"], "/data/display_name"),
        design_status=_text(extension["design_status"], "/data/character/design_status"),
        visual_reference_path=_relative_path(
            extension["visual_reference_path"],
            "/data/character/visual_reference_path",
        ),
        scale_anchor=_text(extension["scale_anchor"], "/data/character/scale_anchor"),
        invariants=_strings(extension["invariants"], "/data/character/invariants"),
    )


def _location_data(value: Mapping[str, object]) -> LocationData:
    _reject_unknown(value, {"display_name", "location"}, "/data")
    _require(value, {"display_name", "location"}, "/data")
    extension = _mapping(value["location"], "/data/location")
    allowed = {"reference_tokens", "usage", "notes"}
    _reject_unknown(extension, allowed, "/data/location")
    _require(extension, allowed, "/data/location")
    return LocationData(
        display_name=_text(value["display_name"], "/data/display_name"),
        reference_tokens=_strings(extension["reference_tokens"], "/data/location/reference_tokens"),
        usage=_text(extension["usage"], "/data/location/usage"),
        notes=_strings(extension["notes"], "/data/location/notes", allow_empty=True),
    )


def entity_from_mapping(data: Mapping[str, object]) -> BrandEntity:
    """Validate a closed entity mapping while preserving declared list order."""

    document = _mapping(data, "/")
    allowed = {
        "artifact_version",
        "entity_id",
        "entity_kind",
        "canonical_status",
        "source",
        "rules_version",
        "fixed_sentences",
        "data",
    }
    _reject_unknown(document, allowed, "/")
    _require(document, allowed, "/")
    if document["artifact_version"] != BRAND_ENTITY_ARTIFACT_VERSION:
        raise _fail(f"/artifact_version must be {BRAND_ENTITY_ARTIFACT_VERSION}")
    try:
        kind = EntityKind(document["entity_kind"])
    except (TypeError, ValueError) as error:
        raise _fail("/entity_kind must be character or location") from error
    try:
        status = CanonicalStatus(document["canonical_status"])
    except (TypeError, ValueError) as error:
        raise _fail("/canonical_status must be shadow or canonical") from error
    raw_data = _mapping(document["data"], "/data")
    typed_data: BrandData
    if kind is EntityKind.CHARACTER:
        typed_data = _character_data(raw_data)
    else:
        typed_data = _location_data(raw_data)
    return BrandEntity(
        artifact_version=BRAND_ENTITY_ARTIFACT_VERSION,
        entity_id=_opaque_id(document["entity_id"], "/entity_id"),
        entity_kind=kind,
        canonical_status=status,
        source=_source(document["source"]),
        rules_version=_text(document["rules_version"], "/rules_version"),
        fixed_sentences=_fixed_sentences(document["fixed_sentences"]),
        data=typed_data,
    )


def entity_to_mapping(entity: BrandEntity) -> dict[str, object]:
    """Return the schema-shaped mapping without sorting or combining ordered records."""

    source = {
        "path": entity.source.path,
        "sha256": entity.source.sha256,
        "section_ranges": [
            {
                "start_heading": item.start_heading,
                "end_before_heading": item.end_before_heading,
            }
            for item in entity.source.section_ranges
        ],
    }
    fixed_sentences = [
        {
            "role": item.role,
            "consumers": list(item.consumers),
            "text": item.text,
            "source_section": item.source_section,
            "sha256": item.sha256,
        }
        for item in entity.fixed_sentences
    ]
    if isinstance(entity.data, CharacterData):
        mapped_data: dict[str, object] = {
            "display_name": entity.data.display_name,
            "character": {
                "design_status": entity.data.design_status,
                "visual_reference_path": entity.data.visual_reference_path,
                "scale_anchor": entity.data.scale_anchor,
                "invariants": list(entity.data.invariants),
            },
        }
    else:
        mapped_data = {
            "display_name": entity.data.display_name,
            "location": {
                "reference_tokens": list(entity.data.reference_tokens),
                "usage": entity.data.usage,
                "notes": list(entity.data.notes),
            },
        }
    return {
        "artifact_version": entity.artifact_version,
        "entity_id": entity.entity_id,
        "entity_kind": entity.entity_kind.value,
        "canonical_status": entity.canonical_status.value,
        "source": source,
        "rules_version": entity.rules_version,
        "fixed_sentences": fixed_sentences,
        "data": mapped_data,
    }
