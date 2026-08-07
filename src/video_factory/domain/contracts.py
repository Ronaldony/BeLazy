"""Serializable primitive shapes shared by all core ports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NewType, Protocol

OpaqueId = NewType("OpaqueId", str)
RequestId = NewType("RequestId", str)
CapabilityId = NewType("CapabilityId", str)
RoleId = NewType("RoleId", str)
HashDigest = NewType("HashDigest", str)
IdempotencyKey = NewType("IdempotencyKey", str)
ArtifactVersion = NewType("ArtifactVersion", str)
MigrationId = NewType("MigrationId", str)
RelativeArtifactPath = NewType("RelativeArtifactPath", str)


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    """Reference to immutable artifact bytes using a repository-relative path."""

    path: RelativeArtifactPath
    sha256: HashDigest
    artifact_version: ArtifactVersion


@dataclass(frozen=True, slots=True)
class MigrationRequest:
    source: ArtifactReference
    migration_id: MigrationId
    target_version: ArtifactVersion


@dataclass(frozen=True, slots=True)
class MigrationResult:
    source: ArtifactReference
    output: ArtifactReference
    migration_id: MigrationId
    core_version: str


class ContractValidator(Protocol):
    def validate(self, artifact: ArtifactReference, data: bytes) -> tuple[str, ...]: ...


class ArtifactMigrator(Protocol):
    def migrate(self, request: MigrationRequest, source_bytes: bytes) -> MigrationResult: ...


class CanonicalSerializer(Protocol):
    def serialize(self, value: object, artifact_version: ArtifactVersion) -> bytes: ...
