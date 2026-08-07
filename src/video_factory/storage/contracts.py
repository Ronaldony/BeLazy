"""No-overwrite storage and exact-catalog access contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    HashDigest,
    OpaqueId,
    RelativeArtifactPath,
)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    maximum_attempts: int
    retryable_error_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class VersionAllocation:
    path: RelativeArtifactPath
    version_ordinal: int


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    record_id: OpaqueId
    sequence: int
    artifact: ArtifactReference


@dataclass(frozen=True, slots=True)
class LedgerRecord:
    record_id: OpaqueId
    sequence: int
    payload_sha256: HashDigest
    artifact_version: ArtifactVersion


class ArtifactStore(Protocol):
    def load(self, reference: ArtifactReference) -> bytes: ...

    def allocate_version(
        self,
        path_prefix: RelativeArtifactPath,
        suffix: str,
    ) -> VersionAllocation: ...

    def write_new(
        self,
        allocation: VersionAllocation,
        data: bytes,
        artifact_version: ArtifactVersion,
    ) -> ArtifactReference: ...


class CatalogReader(Protocol):
    def entries(self) -> Sequence[CatalogEntry]: ...

    def get(self, record_id: OpaqueId) -> CatalogEntry: ...


class CompositeCatalogReader(Protocol):
    def get_unique(self, record_id: OpaqueId) -> CatalogEntry: ...


class ImmutableLedgerWriter(Protocol):
    def append(self, record: LedgerRecord, data: bytes) -> ArtifactReference: ...

