"""Encode command builder tests — plan only, never executes."""

from __future__ import annotations

from pathlib import Path

from video_factory.domain import HashDigest, OpaqueId, RelativeArtifactPath
from video_factory.encode import (
    EncodeInputClip,
    EncodeInputRole,
    EncodePlanStatus,
    EncodeRequest,
    OverwritePolicy,
    build_encode_command,
    default_encode_profiles,
)


SHA = HashDigest("a" * 64)


def _clip(
    path: str = "06_raw/clip01.mp4",
    duration: float | None = 5.0,
    role: EncodeInputRole = EncodeInputRole.VIDEO,
) -> EncodeInputClip:
    return EncodeInputClip(
        path=RelativeArtifactPath(path),
        sha256=SHA,
        duration_sec=duration,
        role=role,
    )


def test_final_profile_determinism() -> None:
    request = EncodeRequest(
        profile_id=OpaqueId("final"),
        inputs=(_clip(),),
        output_path=RelativeArtifactPath("07_edit/final.mp4"),
        output_exists=False,
    )
    plan_a = build_encode_command(request)
    plan_b = build_encode_command(request)
    assert plan_a.status is EncodePlanStatus.PLANNED
    assert plan_a.argv == plan_b.argv
    assert plan_a.command_string == plan_b.command_string
    assert plan_a.executed is False
    assert plan_a.argv[0] == "ffmpeg"
    assert "-n" in plan_a.argv
    assert "libx264" in plan_a.argv
    assert "07_edit/final.mp4" in plan_a.argv


def test_rough_cut_concat_profile() -> None:
    request = EncodeRequest(
        profile_id=OpaqueId("rough_cut"),
        inputs=(
            _clip("06_raw/a.mp4", 4.0),
            _clip("06_raw/b.mp4", 3.5),
        ),
        output_path=RelativeArtifactPath("07_edit/rough_cut.mp4"),
    )
    plan = build_encode_command(request)
    assert plan.status is EncodePlanStatus.PLANNED
    assert plan.profile_kind is not None
    assert plan.profile_kind.value == "concat"
    assert "-filter_complex" in plan.argv
    joined = " ".join(plan.argv)
    assert "concat=n=2" in joined
    assert "-an" in plan.argv


def test_refuse_overwrite_when_output_exists() -> None:
    request = EncodeRequest(
        profile_id=OpaqueId("final"),
        inputs=(_clip(),),
        output_path=RelativeArtifactPath("07_edit/final.mp4"),
        overwrite_policy=OverwritePolicy.REFUSE,
        output_exists=True,
    )
    plan = build_encode_command(request)
    assert plan.status is EncodePlanStatus.REJECTED_OUTPUT_EXISTS
    assert plan.argv == ()
    assert plan.executed is False
    assert plan.rejection_reason is not None
    assert "refuse" in plan.rejection_reason


def test_version_suffix_when_allowed() -> None:
    request = EncodeRequest(
        profile_id=OpaqueId("final"),
        inputs=(_clip(),),
        output_path=RelativeArtifactPath("07_edit/final.mp4"),
        overwrite_policy=OverwritePolicy.ALLOW_VERSION_SUFFIX,
        output_exists=True,
        version_suffix="_v2",
    )
    plan = build_encode_command(request)
    assert plan.status is EncodePlanStatus.PLANNED
    assert plan.argv[-1] == "07_edit/final_v2.mp4"


def test_default_profiles_are_data_and_overridable() -> None:
    profiles = default_encode_profiles()
    assert "rough_cut" in profiles
    assert "final" in profiles
    assert profiles["rough_cut"].kind.value == "concat"
    assert profiles["final"].kind.value == "mux_reencode"

    # Channel override by replacing profile map entry
    custom = dict(profiles)
    overridden = profiles["final"]
    custom["final"] = type(overridden)(
        profile_id=overridden.profile_id,
        kind=overridden.kind,
        defaults=overridden.defaults,
        binary="ffmpeg-custom",
        description="channel override",
    )
    request = EncodeRequest(
        profile_id=OpaqueId("final"),
        inputs=(_clip(),),
        output_path=RelativeArtifactPath("out.mp4"),
    )
    plan = build_encode_command(request, profiles=custom)
    assert plan.argv[0] == "ffmpeg-custom"


def test_final_profile_muxes_separate_audio_and_plans_post_checks() -> None:
    request = EncodeRequest(
        profile_id=OpaqueId("final"),
        inputs=(
            _clip("07_edit/picture.mp4"),
            _clip(
                "07_edit/soundtrack.wav",
                role=EncodeInputRole.AUDIO,
            ),
        ),
        output_path=RelativeArtifactPath("07_edit/final_with_audio.mp4"),
        filter_extra="eq=contrast=1.01",
        audio_filter_extra="loudnorm",
    )
    plan = build_encode_command(request)
    assert plan.status is EncodePlanStatus.PLANNED
    assert plan.profile_kind is not None
    assert plan.profile_kind.value == "mux_reencode"
    joined = " ".join(plan.argv)
    assert "-map 0:v:0" in joined
    assert "-map 1:a:0" in joined
    assert "-shortest" in plan.argv
    assert "eq=contrast=1.01" in joined
    assert "loudnorm" in joined
    assert {str(item.measurement_id) for item in plan.post_encode_checks} == {
        "width",
        "height",
        "has_audio",
    }


def test_encode_modules_have_no_subprocess_or_os_system() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "video_factory" / "encode"
    banned = ("subprocess", "os.system", "os.popen", "Popen")
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in text, f"{path.name} contains banned token {token!r}"

    # Also scan lint and sheets (new modules this phase)
    for package in ("lint", "sheets"):
        pkg = Path(__file__).resolve().parents[2] / "src" / "video_factory" / package
        for path in pkg.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for token in ("subprocess", "os.system"):
                assert token not in text, f"{path} contains {token}"
