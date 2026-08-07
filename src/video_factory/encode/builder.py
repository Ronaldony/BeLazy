"""Deterministic ffmpeg argv / command-string builder — never executes."""

from __future__ import annotations

from collections.abc import Mapping
import shlex
from typing import Iterable

from video_factory.domain import RelativeArtifactPath

from .contracts import (
    EncodeCommandPlan,
    EncodeInputClip,
    EncodeInputRole,
    EncodeOutputSpec,
    EncodePlanStatus,
    EncodeProfile,
    EncodeProfileKind,
    EncodeRequest,
    OverwritePolicy,
    default_encode_profiles,
)


class EncodeBuildError(ValueError):
    """Raised when a request cannot be turned into a plan without guessing."""


def build_encode_command(
    request: EncodeRequest,
    *,
    profiles: Mapping[str, EncodeProfile] | None = None,
) -> EncodeCommandPlan:
    """Build an ffmpeg command plan from *request*.

    This function:
    - never imports process-launch helpers and never starts a child process
    - refuses to plan when overwrite policy is REFUSE and ``output_exists``

    The caller (human or a later approved adapter) is responsible for execution.
    """

    catalog = dict(default_encode_profiles())
    if profiles is not None:
        catalog.update(profiles)

    profile_key = str(request.profile_id)
    profile = catalog.get(profile_key)
    if profile is None:
        return EncodeCommandPlan(
            status=EncodePlanStatus.REJECTED_INVALID,
            profile_id=request.profile_id,
            argv=(),
            command_string="",
            output_path=request.output_path,
            executed=False,
            rejection_reason=f"unknown encode profile_id: {profile_key}",
        )
    missing_fallbacks = [
        str(item)
        for item in profile.fallback_profile_ids
        if str(item) not in catalog
    ]
    if missing_fallbacks:
        return _invalid_plan(
            request,
            profile,
            "encode profile references unknown fallback profiles: "
            + ", ".join(missing_fallbacks),
        )

    if not request.inputs:
        return EncodeCommandPlan(
            status=EncodePlanStatus.REJECTED_INVALID,
            profile_id=request.profile_id,
            argv=(),
            command_string="",
            output_path=request.output_path,
            executed=False,
            rejection_reason="inputs must be non-empty",
            profile_kind=profile.kind,
        )

    output_path = str(request.output_path)
    if request.overwrite_policy is OverwritePolicy.REFUSE and request.output_exists:
        return EncodeCommandPlan(
            status=EncodePlanStatus.REJECTED_OUTPUT_EXISTS,
            profile_id=request.profile_id,
            argv=(),
            command_string="",
            output_path=request.output_path,
            executed=False,
            rejection_reason=(
                f"output already exists and overwrite_policy=refuse: {output_path}"
            ),
            profile_kind=profile.kind,
        )

    if (
        request.overwrite_policy is OverwritePolicy.ALLOW_VERSION_SUFFIX
        and request.output_exists
    ):
        if not request.version_suffix:
            return EncodeCommandPlan(
                status=EncodePlanStatus.REJECTED_INVALID,
                profile_id=request.profile_id,
                argv=(),
                command_string="",
                output_path=request.output_path,
                executed=False,
                rejection_reason=(
                    "output_exists with allow_version_suffix requires version_suffix"
                ),
                profile_kind=profile.kind,
            )
        output_path = _apply_version_suffix(output_path, request.version_suffix)

    spec = request.output_spec if request.output_spec is not None else profile.defaults
    binary = request.binary if request.binary is not None else profile.binary

    if profile.kind is EncodeProfileKind.CONCAT:
        if any(item.role is not EncodeInputRole.VIDEO for item in request.inputs):
            return _invalid_plan(
                request, profile, "concat profile accepts video-role inputs only"
            )
        argv = _build_concat_argv(
            binary,
            request.inputs,
            output_path,
            spec,
            filter_extra=request.filter_extra,
        )
    elif profile.kind is EncodeProfileKind.SINGLE_REENCODE:
        if (
            len(request.inputs) != 1
            or request.inputs[0].role is not EncodeInputRole.VIDEO
        ):
            return _invalid_plan(
                request,
                profile,
                "single_reencode profile requires exactly one video-role input",
            )
        argv = _build_single_argv(
            binary,
            request.inputs[0],
            output_path,
            spec,
            filter_extra=request.filter_extra,
            audio_filter_extra=request.audio_filter_extra,
        )
    elif profile.kind is EncodeProfileKind.MUX_REENCODE:
        videos = [
            (index, item)
            for index, item in enumerate(request.inputs)
            if item.role is EncodeInputRole.VIDEO
        ]
        audios = [
            (index, item)
            for index, item in enumerate(request.inputs)
            if item.role is EncodeInputRole.AUDIO
        ]
        unsupported = [
            item.role.value
            for item in request.inputs
            if item.role not in {EncodeInputRole.VIDEO, EncodeInputRole.AUDIO}
        ]
        if len(videos) != 1 or len(audios) > 1 or unsupported:
            return _invalid_plan(
                request,
                profile,
                "mux_reencode requires one video, at most one audio, and no "
                f"unsupported roles; unsupported={unsupported}",
            )
        argv = _build_mux_argv(
            binary,
            request.inputs,
            video_index=videos[0][0],
            audio_index=audios[0][0] if audios else None,
            output_path=output_path,
            spec=spec,
            filter_extra=request.filter_extra,
            audio_filter_extra=request.audio_filter_extra,
            shortest=request.shortest,
        )
    else:
        return EncodeCommandPlan(
            status=EncodePlanStatus.REJECTED_INVALID,
            profile_id=request.profile_id,
            argv=(),
            command_string="",
            output_path=request.output_path,
            executed=False,
            rejection_reason=f"unsupported profile kind: {profile.kind}",
            profile_kind=profile.kind,
        )

    argv_tuple = tuple(argv)
    return EncodeCommandPlan(
        status=EncodePlanStatus.PLANNED,
        profile_id=request.profile_id,
        argv=argv_tuple,
        command_string=_command_string(argv_tuple),
        output_path=RelativeArtifactPath(output_path),
        executed=False,
        rejection_reason=None,
        profile_kind=profile.kind,
        post_encode_checks=profile.post_encode_checks,
        fallback_profile_ids=profile.fallback_profile_ids,
    )


def _invalid_plan(
    request: EncodeRequest,
    profile: EncodeProfile,
    reason: str,
) -> EncodeCommandPlan:
    return EncodeCommandPlan(
        status=EncodePlanStatus.REJECTED_INVALID,
        profile_id=request.profile_id,
        argv=(),
        command_string="",
        output_path=request.output_path,
        executed=False,
        rejection_reason=reason,
        profile_kind=profile.kind,
        post_encode_checks=profile.post_encode_checks,
        fallback_profile_ids=profile.fallback_profile_ids,
    )


def _apply_version_suffix(path: str, suffix: str) -> str:
    if path.lower().endswith(".mp4"):
        return f"{path[:-4]}{suffix}.mp4"
    return f"{path}{suffix}"


def _scale_pad_filter(
    spec: EncodeOutputSpec,
    filter_extra: str | None = None,
) -> str:
    w, h = spec.width, spec.height
    parts = [
        f"scale={w}:{h}:force_original_aspect_ratio=decrease",
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
        "setsar=1",
    ]
    if spec.fps is not None:
        # format fps without redundant float noise when integral
        fps_text = str(int(spec.fps)) if float(spec.fps).is_integer() else str(spec.fps)
        parts.append(f"fps={fps_text}")
    if filter_extra:
        parts.append(filter_extra)
    return ",".join(parts)


def _build_single_argv(
    binary: str,
    clip: EncodeInputClip,
    output_path: str,
    spec: EncodeOutputSpec,
    *,
    filter_extra: str | None,
    audio_filter_extra: str | None,
) -> list[str]:
    argv: list[str] = [
        binary,
        "-n",
        "-i",
        str(clip.path),
        "-map",
        "0:v:0",
    ]
    if spec.include_audio and spec.audio_codec:
        argv.extend(["-map", "0:a?"])
    else:
        argv.append("-an")

    argv.extend(["-vf", _scale_pad_filter(spec, filter_extra)])
    argv.extend(_video_encode_args(spec))
    if spec.include_audio and spec.audio_codec:
        if audio_filter_extra:
            argv.extend(["-af", audio_filter_extra])
        argv.extend(_audio_encode_args(spec))
    if spec.movflags_faststart:
        argv.extend(["-movflags", "+faststart"])
    argv.append(output_path)
    return argv


def _build_concat_argv(
    binary: str,
    inputs: Iterable[EncodeInputClip],
    output_path: str,
    spec: EncodeOutputSpec,
    *,
    filter_extra: str | None,
) -> list[str]:
    clips = list(inputs)
    argv: list[str] = [binary, "-n"]
    for clip in clips:
        argv.extend(["-i", str(clip.path)])

    filters: list[str] = []
    labels: list[str] = []
    for index, clip in enumerate(clips):
        label = f"v{index}"
        labels.append(f"[{label}]")
        duration = clip.duration_sec
        filter_parts = [
            f"[{index}:v:0]{_scale_pad_filter(spec, filter_extra)}",
        ]
        if duration is not None:
            duration_text = f"{float(duration):.3f}"
            filter_parts.append(
                f"tpad=stop_mode=clone:stop_duration={duration_text}"
            )
            filter_parts.append(f"trim=duration={duration_text}")
        filter_parts.append("setpts=PTS-STARTPTS")
        filters.append(",".join(filter_parts) + f"[{label}]")
    filters.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[outv]")

    argv.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[outv]",
            "-an",
            *_video_encode_args(spec),
        ]
    )
    if spec.movflags_faststart:
        argv.extend(["-movflags", "+faststart"])
    argv.append(output_path)
    return argv


def _build_mux_argv(
    binary: str,
    inputs: tuple[EncodeInputClip, ...],
    *,
    video_index: int,
    audio_index: int | None,
    output_path: str,
    spec: EncodeOutputSpec,
    filter_extra: str | None,
    audio_filter_extra: str | None,
    shortest: bool,
) -> list[str]:
    argv: list[str] = [binary, "-n"]
    for clip in inputs:
        argv.extend(["-i", str(clip.path)])
    argv.extend(
        [
            "-map",
            f"{video_index}:v:0",
            "-vf",
            _scale_pad_filter(spec, filter_extra),
        ]
    )
    if spec.include_audio and spec.audio_codec:
        if audio_index is None:
            argv.extend(["-map", f"{video_index}:a?"])
        else:
            argv.extend(["-map", f"{audio_index}:a:0"])
        if audio_filter_extra:
            argv.extend(["-af", audio_filter_extra])
        argv.extend(_audio_encode_args(spec))
        if audio_index is not None and shortest:
            argv.append("-shortest")
    else:
        argv.append("-an")
    argv.extend(_video_encode_args(spec))
    if spec.movflags_faststart:
        argv.extend(["-movflags", "+faststart"])
    argv.append(output_path)
    return argv


def _video_encode_args(spec: EncodeOutputSpec) -> list[str]:
    return [
        "-c:v",
        spec.video_codec,
        "-preset",
        spec.video_preset,
        "-crf",
        str(spec.video_crf),
        "-pix_fmt",
        spec.pixel_format,
    ]


def _audio_encode_args(spec: EncodeOutputSpec) -> list[str]:
    args = ["-c:a", str(spec.audio_codec)]
    if spec.audio_bitrate:
        args.extend(["-b:a", spec.audio_bitrate])
    if spec.audio_sample_rate is not None:
        args.extend(["-ar", str(spec.audio_sample_rate)])
    if spec.audio_channels is not None:
        args.extend(["-ac", str(spec.audio_channels)])
    return args


def _command_string(argv: tuple[str, ...]) -> str:
    """POSIX-style shell join for display; argv list is authoritative."""

    return shlex.join(argv)
