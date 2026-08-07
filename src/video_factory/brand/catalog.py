"""Exact-source catalog port and the stage-A Markdown implementation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
import hashlib
from pathlib import Path, PurePosixPath
from typing import Protocol

from video_factory.domain import HashDigest, OpaqueId, RelativeArtifactPath

from .models import BrandEntity, CanonicalStatus, EntityKind, FixedSentence


class CatalogSourceFormat(str, Enum):
    MARKDOWN = "markdown"
    STRUCTURED = "structured"


class NonProductionEntityError(ValueError):
    """Raised when a non-canonical structured projection is offered to production."""


class FixedSentenceExtractor(Protocol):
    def __call__(self, markdown: bytes) -> Sequence[FixedSentence]: ...


@dataclass(frozen=True, slots=True)
class MarkdownCatalogEntry:
    """One exact Markdown source; no discovery, fallback, or mtime selection is allowed."""

    entity_id: OpaqueId
    entity_kind: EntityKind
    path: RelativeArtifactPath
    sha256: HashDigest
    extractor: FixedSentenceExtractor = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class CatalogEntity:
    entity_id: OpaqueId
    entity_kind: EntityKind
    source_format: CatalogSourceFormat
    path: RelativeArtifactPath
    sha256: HashDigest
    payload: bytes
    fixed_sentences: tuple[FixedSentence, ...]


class BrandCatalog(Protocol):
    def get(self, entity_id: OpaqueId) -> CatalogEntity: ...

    def fixed_sentences(self, entity_id: OpaqueId) -> tuple[FixedSentence, ...]: ...


class MarkdownBrandCatalog:
    """Production catalog that reads owner-declared Markdown paths only."""

    def __init__(self, root: Path, entries: Sequence[MarkdownCatalogEntry]) -> None:
        self._root = root.resolve()
        self._entries: dict[str, MarkdownCatalogEntry] = {}
        for entry in entries:
            key = str(entry.entity_id)
            if key in self._entries:
                raise ValueError(f"duplicate catalog entity: {key}")
            self._entries[key] = entry

    def get(self, entity_id: OpaqueId) -> CatalogEntity:
        key = str(entity_id)
        try:
            entry = self._entries[key]
        except KeyError as error:
            raise KeyError(f"catalog entity is not declared: {key}") from error
        relative = PurePosixPath(str(entry.path))
        candidate = self._root.joinpath(*relative.parts).resolve(strict=True)
        try:
            candidate.relative_to(self._root)
        except ValueError as error:
            raise ValueError("catalog path escapes its declared root") from error
        payload = candidate.read_bytes()
        actual = hashlib.sha256(payload).hexdigest()
        if actual != entry.sha256:
            raise ValueError(f"catalog source hash mismatch for entity: {key}")
        fixed_sentences = tuple(entry.extractor(payload))
        if not all(isinstance(item, FixedSentence) for item in fixed_sentences):
            raise TypeError("catalog extractor returned a non-fixed-sentence value")
        return CatalogEntity(
            entity_id=entry.entity_id,
            entity_kind=entry.entity_kind,
            source_format=CatalogSourceFormat.MARKDOWN,
            path=entry.path,
            sha256=entry.sha256,
            payload=payload,
            fixed_sentences=fixed_sentences,
        )

    def fixed_sentences(self, entity_id: OpaqueId) -> tuple[FixedSentence, ...]:
        return self.get(entity_id).fixed_sentences


def require_production_eligible(entity: BrandEntity) -> BrandEntity:
    """Reject stage-A shadow data instead of silently using it as production input."""

    if entity.canonical_status is not CanonicalStatus.CANONICAL:
        raise NonProductionEntityError(
            f"structured entity is not production-eligible: {entity.canonical_status.value}"
        )
    return entity
