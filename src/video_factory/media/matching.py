"""Pure matching for caller-observed generated media files."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from video_factory.domain import HashDigest, RelativeArtifactPath


class MediaMatchStatus(StrEnum):
    MATCHED = "matched"
    MATCHED_WITH_WARNING = "matched_with_warning"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ObservedMediaFile:
    path: RelativeArtifactPath
    sha256: HashDigest
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True, slots=True)
class ExpectedMediaOutput:
    path: RelativeArtifactPath
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class MediaMatchFinding:
    severity: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class MediaMatchResult:
    status: MediaMatchStatus
    selected: ObservedMediaFile | None
    findings: tuple[MediaMatchFinding, ...]

    @property
    def accepted(self) -> bool:
        return self.status in {
            MediaMatchStatus.MATCHED,
            MediaMatchStatus.MATCHED_WITH_WARNING,
        }


def _status_from_findings(
    findings: Sequence[MediaMatchFinding],
) -> MediaMatchStatus:
    if any(item.severity == "error" for item in findings):
        return MediaMatchStatus.REJECTED
    if any(item.severity == "warning" for item in findings):
        return MediaMatchStatus.MATCHED_WITH_WARNING
    return MediaMatchStatus.MATCHED


def match_expected_output(
    expected: ExpectedMediaOutput,
    observed: tuple[ObservedMediaFile, ...],
) -> MediaMatchResult:
    """Match exact or accidental double-extension output and check aspect.

    The caller performs filesystem and probe operations. Core only compares
    injected facts and never hard-codes an aspect ratio.
    """

    if expected.width <= 0 or expected.height <= 0:
        raise ValueError("expected media dimensions must be positive")
    expected_path = str(expected.path)
    exact = [item for item in observed if str(item.path) == expected_path]
    doubled = [
        item
        for item in observed
        if expected_path.lower().endswith(".mp4")
        and str(item.path) == f"{expected_path}.mp4"
    ]
    candidates = [*exact, *doubled]
    if not candidates:
        return MediaMatchResult(
            status=MediaMatchStatus.MISSING,
            selected=None,
            findings=(
                MediaMatchFinding(
                    severity="error",
                    code="output_missing",
                    message=f"no observed file matches {expected_path}",
                ),
            ),
        )
    if len(candidates) > 1:
        return MediaMatchResult(
            status=MediaMatchStatus.AMBIGUOUS,
            selected=None,
            findings=(
                MediaMatchFinding(
                    severity="error",
                    code="output_ambiguous",
                    message=(
                        "multiple exact/double-extension files match the expected "
                        "output; caller must select one explicitly"
                    ),
                ),
            ),
        )

    selected = candidates[0]
    findings: list[MediaMatchFinding] = []
    if selected in doubled:
        findings.append(
            MediaMatchFinding(
                severity="warning",
                code="double_mp4_extension",
                message=(
                    f"accepted {selected.path} as {expected.path}; preserve the "
                    "observed filename in provenance"
                ),
            )
        )
    if selected.width is None or selected.height is None:
        findings.append(
            MediaMatchFinding(
                severity="error",
                code="dimensions_unavailable",
                message="observed media dimensions are required for aspect validation",
            )
        )
    elif (
        selected.width <= 0
        or selected.height <= 0
        or selected.width * expected.height
        != expected.width * selected.height
    ):
        findings.append(
            MediaMatchFinding(
                severity="error",
                code="aspect_mismatch",
                message=(
                    f"observed dimensions {selected.width}x{selected.height} do "
                    f"not match expected aspect {expected.width}x{expected.height}"
                ),
            )
        )

    status = _status_from_findings(findings)
    return MediaMatchResult(
        status=status,
        selected=selected,
        findings=tuple(findings),
    )
