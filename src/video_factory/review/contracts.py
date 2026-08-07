"""Hash-bound review and bounded revision shapes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from video_factory.domain import ArtifactReference, HashDigest, OpaqueId, RoleId


class ReviewVerdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class RevisionPolicy:
    maximum_attempts: int
    require_progress: bool
    halt_on_regression: bool
    halt_on_oscillation: bool


@dataclass(frozen=True, slots=True)
class ReviewRequest:
    review_id: OpaqueId
    subject: ArtifactReference
    creator_role: RoleId
    reviewer_role: RoleId
    effective_config_sha256: HashDigest
    output_contract: str


@dataclass(frozen=True, slots=True)
class ReviewResult:
    request: ReviewRequest
    verdict: ReviewVerdict
    report: ArtifactReference
    progress_fingerprint: HashDigest | None


class ReviewPort(Protocol):
    def review(self, request: ReviewRequest) -> ReviewResult: ...

