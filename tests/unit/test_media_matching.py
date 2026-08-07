"""Pure generated-media filename and aspect matching."""

import pytest

from video_factory.media import matching as matching_module
from video_factory.domain import HashDigest, RelativeArtifactPath
from video_factory.media import (
    ExpectedMediaOutput,
    MediaMatchFinding,
    MediaMatchStatus,
    ObservedMediaFile,
    match_expected_output,
)


def _observed(path: str, width: int = 720, height: int = 1280) -> ObservedMediaFile:
    return ObservedMediaFile(
        path=RelativeArtifactPath(path),
        sha256=HashDigest("a" * 64),
        width=width,
        height=height,
    )


def test_double_mp4_extension_is_accepted_with_warning() -> None:
    result = match_expected_output(
        ExpectedMediaOutput(
            path=RelativeArtifactPath("06_generated/shot-01.mp4"),
            width=720,
            height=1280,
        ),
        (_observed("06_generated/shot-01.mp4.mp4"),),
    )
    assert result.status is MediaMatchStatus.MATCHED_WITH_WARNING
    assert result.accepted is True
    assert result.findings[0].code == "double_mp4_extension"


def test_aspect_mismatch_is_rejected_from_injected_dimensions() -> None:
    result = match_expected_output(
        ExpectedMediaOutput(
            path=RelativeArtifactPath("06_generated/shot-01.mp4"),
            width=720,
            height=1280,
        ),
        (_observed("06_generated/shot-01.mp4", 1280, 720),),
    )
    assert result.status is MediaMatchStatus.REJECTED
    assert result.accepted is False
    assert any(item.code == "aspect_mismatch" for item in result.findings)


def test_exact_and_double_extension_together_are_ambiguous() -> None:
    result = match_expected_output(
        ExpectedMediaOutput(
            path=RelativeArtifactPath("06_generated/shot-01.mp4"),
            width=720,
            height=1280,
        ),
        (
            _observed("06_generated/shot-01.mp4"),
            _observed("06_generated/shot-01.mp4.mp4"),
        ),
    )
    assert result.status is MediaMatchStatus.AMBIGUOUS


@pytest.mark.parametrize(
    ("severity", "expected"),
    [
        ("error", MediaMatchStatus.REJECTED),
        ("warning", MediaMatchStatus.MATCHED_WITH_WARNING),
        ("info", MediaMatchStatus.MATCHED),
    ],
)
def test_match_status_is_derived_from_explicit_finding_severity(
    severity: str,
    expected: MediaMatchStatus,
) -> None:
    findings = (
        MediaMatchFinding(
            severity=severity,
            code="synthetic-finding",
            message="synthetic finding for severity classification",
        ),
    )
    assert matching_module._status_from_findings(findings) is expected
