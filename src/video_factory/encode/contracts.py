"""Encode stage contracts: request types, profiles (data), command plans.

This module never executes ffmpeg. It only describes what *would* be run.
OD-005: ffmpeg may be absent; builders produce argv/strings only.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Mapping

from video_factory.domain import HashDigest, OpaqueId, RelativeArtifactPath


class EncodeProfileKind(StrEnum):
    """How inputs are combined — data, not hard-coded channel policy."""

    CONCAT = "concat"
    SINGLE_REENCODE = "single_reencode"
    MUX_REENCODE = "mux_reencode"


class EncodeInputRole(StrEnum):
    VIDEO = "video"
    AUDIO = "audio"
    SUBTITLE = "subtitle"
    OVERLAY = "overlay"


class OverwritePolicy(StrEnum):
    """No-overwrite asset protection (channel 'generation asset' habit)."""

    REFUSE = "refuse"
    ALLOW_VERSION_SUFFIX = "allow_version_suffix"


class EncodePlanStatus(StrEnum):
    PLANNED = "planned"
    REJECTED_OUTPUT_EXISTS = "rejected_output_exists"
    REJECTED_INVALID = "rejected_invalid"


@dataclass(frozen=True, slots=True)
class EncodeInputClip:
    """One input clip bound by relative path + content digest."""

    path: RelativeArtifactPath
    sha256: HashDigest
    duration_sec: float | None = None
    role: EncodeInputRole = EncodeInputRole.VIDEO


@dataclass(frozen=True, slots=True)
class PostEncodeCheck:
    """Injected deterministic check to run after the external encode."""

    check_id: OpaqueId
    measurement_id: OpaqueId
    comparison: str
    operands: tuple[int | float | str | bool, ...]
    severity: str = "error"


@dataclass(frozen=True, slots=True)
class EncodeOutputSpec:
    """Parameterized output media spec — no fixed aspect ratio vocabulary."""

    width: int
    height: int
    fps: float | None = None
    video_codec: str = "libx264"
    audio_codec: str | None = "aac"
    audio_bitrate: str | None = "192k"
    audio_sample_rate: int | None = 48000
    audio_channels: int | None = 2
    pixel_format: str = "yuv420p"
    video_preset: str = "slow"
    video_crf: int = 18
    container: str = "mp4"
    movflags_faststart: bool = True
    include_audio: bool = True


@dataclass(frozen=True, slots=True)
class EncodeProfile:
    """Named encode recipe supplied as data (channel may override)."""

    profile_id: OpaqueId
    kind: EncodeProfileKind
    defaults: EncodeOutputSpec
    binary: str = "ffmpeg"
    description: str = ""
    post_encode_checks: tuple[PostEncodeCheck, ...] = ()
    fallback_profile_ids: tuple[OpaqueId, ...] = ()


def default_encode_profiles() -> dict[str, EncodeProfile]:
    """Reasonable default profiles — fully overridable by the channel.

    Dimensions and codecs are parameters, not purity-sensitive aspect labels.
    """

    common = EncodeOutputSpec(
        width=1080,
        height=1920,
        fps=30.0,
        video_codec="libx264",
        audio_codec="aac",
        audio_bitrate="192k",
        audio_sample_rate=48000,
        audio_channels=2,
        pixel_format="yuv420p",
        video_preset="slow",
        video_crf=18,
        container="mp4",
        movflags_faststart=True,
        include_audio=True,
    )
    rough = replace(common, include_audio=False, audio_codec=None)
    final_checks = (
        PostEncodeCheck(
            check_id=OpaqueId("final-width"),
            measurement_id=OpaqueId("width"),
            comparison="equal",
            operands=(common.width,),
        ),
        PostEncodeCheck(
            check_id=OpaqueId("final-height"),
            measurement_id=OpaqueId("height"),
            comparison="equal",
            operands=(common.height,),
        ),
        PostEncodeCheck(
            check_id=OpaqueId("final-audio"),
            measurement_id=OpaqueId("has_audio"),
            comparison="equal",
            operands=(common.include_audio,),
        ),
    )
    return {
        "rough_cut": EncodeProfile(
            profile_id=OpaqueId("rough_cut"),
            kind=EncodeProfileKind.CONCAT,
            defaults=rough,
            description="Simple multi-clip concat (video-only rough assembly)",
        ),
        "final": EncodeProfile(
            profile_id=OpaqueId("final"),
            kind=EncodeProfileKind.MUX_REENCODE,
            defaults=common,
            description="Final re-encode with optional separate soundtrack mux",
            post_encode_checks=final_checks,
        ),
    }


@dataclass(frozen=True, slots=True)
class EncodeRequest:
    """Request to *plan* an encode (not execute)."""

    profile_id: OpaqueId
    inputs: tuple[EncodeInputClip, ...]
    output_path: RelativeArtifactPath
    output_spec: EncodeOutputSpec | None = None
    binary: str | None = None
    overwrite_policy: OverwritePolicy = OverwritePolicy.REFUSE
    output_exists: bool = False
    version_suffix: str | None = None
    filter_extra: str | None = None
    audio_filter_extra: str | None = None
    shortest: bool = True
    extensions: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EncodeCommandPlan:
    """Deterministic command plan — ``executed`` is always false by contract."""

    status: EncodePlanStatus
    profile_id: OpaqueId
    argv: tuple[str, ...]
    command_string: str
    output_path: RelativeArtifactPath
    executed: bool = False
    rejection_reason: str | None = None
    profile_kind: EncodeProfileKind | None = None
    post_encode_checks: tuple[PostEncodeCheck, ...] = ()
    fallback_profile_ids: tuple[OpaqueId, ...] = ()

    def __post_init__(self) -> None:
        if self.executed is not False:
            object.__setattr__(self, "executed", False)
