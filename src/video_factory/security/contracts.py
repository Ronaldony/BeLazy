"""Security-related ports without local paths or secret values."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from video_factory.domain import OpaqueId, RelativeArtifactPath


@dataclass(frozen=True, slots=True)
class SecretReference:
    reference_id: OpaqueId
    required_capability: OpaqueId


@dataclass(frozen=True, slots=True)
class PurityFinding:
    rule_id: str
    relative_path: RelativeArtifactPath
    line_number: int | None


class PathGuard(Protocol):
    def require_contained(self, path: RelativeArtifactPath) -> RelativeArtifactPath: ...

    def require_writable(self, path: RelativeArtifactPath) -> RelativeArtifactPath: ...


class CorePurityScanner(Protocol):
    def scan(self) -> Sequence[PurityFinding]: ...

