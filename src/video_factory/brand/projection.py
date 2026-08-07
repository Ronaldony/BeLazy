"""Deterministic Markdown projection and structured round-trip comparison."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Iterable

from .models import BrandEntity, CharacterData, FixedSentence, LocationData


_BLOCK_PREFIX = b"<!-- video-factory:block "
_BLOCK_SUFFIX = b" -->\n"
_END_MARKER = b"<!-- video-factory:end -->"


class ProjectionFormatError(ValueError):
    """Raised when a generated view cannot be parsed without guessing."""


@dataclass(frozen=True, slots=True)
class ProjectionField:
    path: str
    value: str


@dataclass(frozen=True, slots=True)
class FixedSentenceObservation:
    role: str
    consumers: tuple[str, ...]
    text: str
    source_section: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ProjectionSnapshot:
    fields: tuple[ProjectionField, ...]
    fixed_sentences: tuple[FixedSentenceObservation, ...]


@dataclass(frozen=True, slots=True)
class FieldComparison:
    path: str
    source_sha256: str | None
    shadow_sha256: str | None
    rendered_sha256: str | None
    issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FixedSentenceComparison:
    role: str
    source_sha256: str | None
    shadow_sha256: str | None
    rendered_sha256: str | None
    issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OrderComparison:
    source_field_order: tuple[str, ...]
    shadow_field_order: tuple[str, ...]
    rendered_field_order: tuple[str, ...]
    source_shadow_fields_match: bool
    shadow_rendered_fields_match: bool
    source_fixed_order: tuple[str, ...]
    shadow_fixed_order: tuple[str, ...]
    rendered_fixed_order: tuple[str, ...]
    source_shadow_fixed_match: bool
    shadow_rendered_fixed_match: bool


@dataclass(frozen=True, slots=True)
class RoundTripReport:
    fields: tuple[FieldComparison, ...]
    fixed_sentences: tuple[FixedSentenceComparison, ...]
    order: OrderComparison

    @property
    def passed(self) -> bool:
        return (
            not any(item.issues for item in self.fields)
            and not any(item.issues for item in self.fixed_sentences)
            and self.order.source_shadow_fields_match
            and self.order.shadow_rendered_fields_match
            and self.order.source_shadow_fixed_match
            and self.order.shadow_rendered_fixed_match
        )

    def as_mapping(self) -> dict[str, object]:
        """Expose a stable structured report without hiding any comparison issue."""

        return {
            "passed": self.passed,
            "fields": [
                {
                    "path": item.path,
                    "source_sha256": item.source_sha256,
                    "shadow_sha256": item.shadow_sha256,
                    "rendered_sha256": item.rendered_sha256,
                    "issues": list(item.issues),
                }
                for item in self.fields
            ],
            "fixed_sentences": [
                {
                    "role": item.role,
                    "source_sha256": item.source_sha256,
                    "shadow_sha256": item.shadow_sha256,
                    "rendered_sha256": item.rendered_sha256,
                    "issues": list(item.issues),
                }
                for item in self.fixed_sentences
            ],
            "order": {
                "source_field_order": list(self.order.source_field_order),
                "shadow_field_order": list(self.order.shadow_field_order),
                "rendered_field_order": list(self.order.rendered_field_order),
                "source_shadow_fields_match": self.order.source_shadow_fields_match,
                "shadow_rendered_fields_match": self.order.shadow_rendered_fields_match,
                "source_fixed_order": list(self.order.source_fixed_order),
                "shadow_fixed_order": list(self.order.shadow_fixed_order),
                "rendered_fixed_order": list(self.order.rendered_fixed_order),
                "source_shadow_fixed_match": self.order.source_shadow_fixed_match,
                "shadow_rendered_fixed_match": self.order.shadow_rendered_fixed_match,
            },
        }


def _fixed_observation(sentence: FixedSentence) -> FixedSentenceObservation:
    return FixedSentenceObservation(
        role=str(sentence.role),
        consumers=tuple(str(item) for item in sentence.consumers),
        text=sentence.text,
        source_section=sentence.source_section,
    )


def entity_snapshot(entity: BrandEntity) -> ProjectionSnapshot:
    """Flatten only modeled owner data; provenance metadata is validated separately."""

    data = entity.data
    fields = [ProjectionField("/data/display_name", data.display_name)]
    if isinstance(data, CharacterData):
        fields.extend(
            (
                ProjectionField("/data/character/design_status", data.design_status),
                ProjectionField(
                    "/data/character/visual_reference_path",
                    str(data.visual_reference_path),
                ),
                ProjectionField("/data/character/scale_anchor", data.scale_anchor),
            )
        )
        fields.extend(
            ProjectionField(f"/data/character/invariants/{index}", value)
            for index, value in enumerate(data.invariants)
        )
    elif isinstance(data, LocationData):
        fields.extend(
            ProjectionField(f"/data/location/reference_tokens/{index}", value)
            for index, value in enumerate(data.reference_tokens)
        )
        fields.append(ProjectionField("/data/location/usage", data.usage))
        fields.extend(
            ProjectionField(f"/data/location/notes/{index}", value)
            for index, value in enumerate(data.notes)
        )
    else:  # pragma: no cover - the closed type is enforced by the model parser.
        raise TypeError(f"unsupported entity data: {type(data).__name__}")
    return ProjectionSnapshot(
        fields=tuple(fields),
        fixed_sentences=tuple(_fixed_observation(item) for item in entity.fixed_sentences),
    )


def _fence(payload: bytes) -> bytes:
    runs = [len(match.group(0)) for match in re.finditer(rb"`+", payload)]
    return b"`" * max(4, max(runs, default=0) + 1)


def _block(kind: str, metadata: dict[str, object], text: str) -> bytes:
    payload = text.encode("utf-8")
    fence = _fence(payload)
    header = dict(metadata)
    header["kind"] = kind
    header["byte_length"] = len(payload)
    marker = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return b"".join(
        (
            _BLOCK_PREFIX,
            marker,
            _BLOCK_SUFFIX,
            fence,
            b"text\n",
            payload,
            b"\n",
            fence,
            b"\n",
            _END_MARKER,
            b"\n",
        )
    )


class MarkdownProjectionRenderer:
    """Render a deterministic view while copying every fixed block byte-for-byte."""

    def render(self, entity: BrandEntity) -> bytes:
        snapshot = entity_snapshot(entity)
        chunks = [
            f"# {entity.data.display_name}\n\n".encode("utf-8"),
            b"> Generated projection view. Authority remains an owner-workspace decision.\n\n",
            f"- artifact_version: `{entity.artifact_version}`\n".encode("utf-8"),
            f"- entity_id: `{entity.entity_id}`\n".encode("utf-8"),
            f"- entity_kind: `{entity.entity_kind.value}`\n".encode("utf-8"),
            f"- canonical_status: `{entity.canonical_status.value}`\n".encode("utf-8"),
            f"- source: `{entity.source.path}` (`{entity.source.sha256}`)\n".encode("utf-8"),
            f"- rules_version: `{entity.rules_version}`\n\n".encode("utf-8"),
            b"## Projected fields\n\n",
        ]
        for field in snapshot.fields:
            chunks.append(f"### `{field.path}`\n\n".encode("utf-8"))
            chunks.append(_block("field", {"path": field.path}, field.value))
            chunks.append(b"\n")
        chunks.append(b"## Fixed sentences\n\n")
        for sentence in snapshot.fixed_sentences:
            consumers = ", ".join(f"`{item}`" for item in sentence.consumers)
            chunks.extend(
                (
                    f"### `{sentence.role}`\n\n".encode("utf-8"),
                    f"- consumers: {consumers}\n".encode("utf-8"),
                    f"- source_section: `{sentence.source_section}`\n".encode("utf-8"),
                    f"- sha256: `{sentence.sha256}`\n\n".encode("utf-8"),
                    _block(
                        "fixed",
                        {
                            "role": sentence.role,
                            "consumers": list(sentence.consumers),
                            "source_section": sentence.source_section,
                            "sha256": sentence.sha256,
                        },
                        sentence.text,
                    ),
                    b"\n",
                )
            )
        return b"".join(chunks)


def rendered_snapshot(rendered: bytes) -> ProjectionSnapshot:
    """Read only explicit renderer blocks; malformed views fail instead of recovering."""

    fields: list[ProjectionField] = []
    fixed: list[FixedSentenceObservation] = []
    cursor = 0
    while True:
        marker_start = rendered.find(_BLOCK_PREFIX, cursor)
        if marker_start < 0:
            break
        metadata_start = marker_start + len(_BLOCK_PREFIX)
        metadata_end = rendered.find(_BLOCK_SUFFIX, metadata_start)
        if metadata_end < 0:
            raise ProjectionFormatError("unterminated projection block metadata")
        try:
            metadata = json.loads(rendered[metadata_start:metadata_end].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProjectionFormatError("invalid projection block metadata") from error
        if not isinstance(metadata, dict):
            raise ProjectionFormatError("projection block metadata must be an object")
        payload_open = metadata_end + len(_BLOCK_SUFFIX)
        opener_end = rendered.find(b"\n", payload_open)
        if opener_end < 0:
            raise ProjectionFormatError("projection block fence is missing")
        opener = rendered[payload_open:opener_end]
        if not opener.endswith(b"text") or set(opener[:-4]) != {96} or len(opener[:-4]) < 4:
            raise ProjectionFormatError("projection block fence is invalid")
        fence = opener[:-4]
        byte_length = metadata.get("byte_length")
        if isinstance(byte_length, bool) or not isinstance(byte_length, int) or byte_length < 0:
            raise ProjectionFormatError("projection block byte_length is invalid")
        payload_start = opener_end + 1
        payload_end = payload_start + byte_length
        if payload_end > len(rendered):
            raise ProjectionFormatError("projection block payload is truncated")
        trailer = b"\n" + fence + b"\n" + _END_MARKER
        if rendered[payload_end : payload_end + len(trailer)] != trailer:
            raise ProjectionFormatError("projection block trailer or byte_length does not match")
        try:
            text = rendered[payload_start:payload_end].decode("utf-8")
        except UnicodeDecodeError as error:
            raise ProjectionFormatError("projection block payload is not UTF-8") from error
        kind = metadata.get("kind")
        if kind == "field":
            path = metadata.get("path")
            if not isinstance(path, str) or not path.startswith("/"):
                raise ProjectionFormatError("field path metadata is invalid")
            fields.append(ProjectionField(path=path, value=text))
        elif kind == "fixed":
            role = metadata.get("role")
            consumers = metadata.get("consumers")
            source_section = metadata.get("source_section")
            if (
                not isinstance(role, str)
                or not isinstance(consumers, list)
                or not consumers
                or not all(isinstance(item, str) for item in consumers)
                or not isinstance(source_section, str)
            ):
                raise ProjectionFormatError("fixed-sentence metadata is invalid")
            fixed.append(
                FixedSentenceObservation(
                    role=role,
                    consumers=tuple(consumers),
                    text=text,
                    source_section=source_section,
                )
            )
        else:
            raise ProjectionFormatError("unknown projection block kind")
        cursor = payload_end + len(trailer)
    if not fields and not fixed:
        raise ProjectionFormatError("no projection blocks found")
    return ProjectionSnapshot(fields=tuple(fields), fixed_sentences=tuple(fixed))


def _unique(items: Iterable[object], key_name: str, value_getter) -> dict[str, object]:
    result: dict[str, object] = {}
    for item in items:
        value = value_getter(item)
        if value in result:
            raise ValueError(f"duplicate {key_name}: {value}")
        result[value] = item
    return result


def _ordered_union(*orders: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    for order in orders:
        for item in order:
            if item not in result:
                result.append(item)
    return tuple(result)


def _hash(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class RoundTripChecker:
    """Compare source extraction, structured shadow, and rendered view without repair."""

    def compare(
        self,
        source: ProjectionSnapshot,
        shadow: ProjectionSnapshot,
        rendered: ProjectionSnapshot,
    ) -> RoundTripReport:
        source_fields = _unique(source.fields, "field path", lambda item: item.path)
        shadow_fields = _unique(shadow.fields, "field path", lambda item: item.path)
        rendered_fields = _unique(rendered.fields, "field path", lambda item: item.path)
        source_field_order = tuple(item.path for item in source.fields)
        shadow_field_order = tuple(item.path for item in shadow.fields)
        rendered_field_order = tuple(item.path for item in rendered.fields)

        field_results: list[FieldComparison] = []
        for path in _ordered_union(source_field_order, shadow_field_order, rendered_field_order):
            source_item = source_fields.get(path)
            shadow_item = shadow_fields.get(path)
            rendered_item = rendered_fields.get(path)
            issues: list[str] = []
            if source_item is None:
                issues.append("missing_in_source")
            if shadow_item is None:
                issues.append("missing_in_shadow")
            if rendered_item is None:
                issues.append("missing_in_rendered")
            source_value = source_item.value if isinstance(source_item, ProjectionField) else None
            shadow_value = shadow_item.value if isinstance(shadow_item, ProjectionField) else None
            rendered_value = rendered_item.value if isinstance(rendered_item, ProjectionField) else None
            if source_value is not None and shadow_value is not None and source_value != shadow_value:
                issues.append("source_shadow_value_mismatch")
            if shadow_value is not None and rendered_value is not None and shadow_value != rendered_value:
                issues.append("shadow_rendered_value_mismatch")
            field_results.append(
                FieldComparison(
                    path=path,
                    source_sha256=_hash(source_value),
                    shadow_sha256=_hash(shadow_value),
                    rendered_sha256=_hash(rendered_value),
                    issues=tuple(issues),
                )
            )

        source_fixed = _unique(source.fixed_sentences, "fixed role", lambda item: item.role)
        shadow_fixed = _unique(shadow.fixed_sentences, "fixed role", lambda item: item.role)
        rendered_fixed = _unique(rendered.fixed_sentences, "fixed role", lambda item: item.role)
        source_fixed_order = tuple(item.role for item in source.fixed_sentences)
        shadow_fixed_order = tuple(item.role for item in shadow.fixed_sentences)
        rendered_fixed_order = tuple(item.role for item in rendered.fixed_sentences)

        fixed_results: list[FixedSentenceComparison] = []
        for role in _ordered_union(source_fixed_order, shadow_fixed_order, rendered_fixed_order):
            source_item = source_fixed.get(role)
            shadow_item = shadow_fixed.get(role)
            rendered_item = rendered_fixed.get(role)
            issues = []
            if source_item is None:
                issues.append("missing_in_source")
            if shadow_item is None:
                issues.append("missing_in_shadow")
            if rendered_item is None:
                issues.append("missing_in_rendered")
            observations = (source_item, shadow_item, rendered_item)
            if all(isinstance(item, FixedSentenceObservation) for item in observations):
                typed_source, typed_shadow, typed_rendered = observations
                if typed_source.consumers != typed_shadow.consumers:
                    issues.append("source_shadow_consumers_mismatch")
                if typed_shadow.consumers != typed_rendered.consumers:
                    issues.append("shadow_rendered_consumers_mismatch")
                if typed_source.source_section != typed_shadow.source_section:
                    issues.append("source_shadow_section_mismatch")
                if typed_shadow.source_section != typed_rendered.source_section:
                    issues.append("shadow_rendered_section_mismatch")
                if typed_source.text.encode("utf-8") != typed_shadow.text.encode("utf-8"):
                    issues.append("source_shadow_text_bytes_mismatch")
                if typed_shadow.text.encode("utf-8") != typed_rendered.text.encode("utf-8"):
                    issues.append("shadow_rendered_text_bytes_mismatch")
            fixed_results.append(
                FixedSentenceComparison(
                    role=role,
                    source_sha256=source_item.sha256 if isinstance(source_item, FixedSentenceObservation) else None,
                    shadow_sha256=shadow_item.sha256 if isinstance(shadow_item, FixedSentenceObservation) else None,
                    rendered_sha256=(
                        rendered_item.sha256
                        if isinstance(rendered_item, FixedSentenceObservation)
                        else None
                    ),
                    issues=tuple(issues),
                )
            )

        order = OrderComparison(
            source_field_order=source_field_order,
            shadow_field_order=shadow_field_order,
            rendered_field_order=rendered_field_order,
            source_shadow_fields_match=source_field_order == shadow_field_order,
            shadow_rendered_fields_match=shadow_field_order == rendered_field_order,
            source_fixed_order=source_fixed_order,
            shadow_fixed_order=shadow_fixed_order,
            rendered_fixed_order=rendered_fixed_order,
            source_shadow_fixed_match=source_fixed_order == shadow_fixed_order,
            shadow_rendered_fixed_match=shadow_fixed_order == rendered_fixed_order,
        )
        return RoundTripReport(
            fields=tuple(field_results),
            fixed_sentences=tuple(fixed_results),
            order=order,
        )
