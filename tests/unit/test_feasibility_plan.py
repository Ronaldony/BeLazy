"""Generic generation feasibility checks from injected capability facts."""

from __future__ import annotations

from decimal import Decimal

from video_factory.artifacts import validate_artifact
from video_factory.domain import CapabilityId, HashDigest, OpaqueId
from video_factory.engine import (
    artifact_reference_to_mapping,
    make_artifact_snapshot,
)
from video_factory.feasibility import (
    FeasibilityStatus,
    evaluate_generation_feasibility,
    feasibility_review_to_mapping,
)
from video_factory.providers import (
    CapabilityConstraintProfile,
    FirstFrameAspectBehavior,
)


def _inputs(
    *,
    duration: float = 5,
    state: str = "before",
    width: int = 720,
) -> tuple[object, object]:
    storyboard = make_artifact_snapshot(
        "02_storyboard/storyboard.json",
        {
            "artifact_version": "storyboard/1.0",
            "rules_version": "rules-test",
            "episode_id": "ep-1",
            "generated_by": "role:writer",
            "premise": "test",
            "shots": [
                {
                    "shot_id": "shot-1",
                    "duration_sec": 5,
                    "narrative_role": "hook",
                    "characters": [],
                    "action": "One action.",
                    "creative_direction": "Plain.",
                    "start_state": "before",
                    "end_state": "after",
                }
            ],
        },
    )
    packet = make_artifact_snapshot(
        "04_prompts/packet.json",
        {
            "artifact_version": "generation-packet/2.0",
            "rules_version": "rules-test",
            "episode_id": "ep-1",
            "generated_by": "role:writer",
            "approved_by_human": False,
            "storyboard_ref": artifact_reference_to_mapping(storyboard.reference),
            "output": {
                "dir": "06_generated",
                "filename_pattern": "{shot}.mp4",
                "target": {"width": 720, "height": 1280},
            },
            "shots": [
                {
                    "shot_id": "shot-1",
                    "source_shot_id": "shot-1",
                    "duration_sec": duration,
                    "candidates": 1,
                    "capability_id": "cap-generate",
                    "prompt": "One action.",
                    "first_frame": {
                        "path": "05_references/before.png",
                        "sha256": "a" * 64,
                        "width": width,
                        "height": 1280,
                        "state": state,
                    },
                    "continuity": {"required": False},
                    "render_dependencies": [
                        {"feature_id": "screen-text", "handled_in": "edit"}
                    ],
                }
            ],
        },
    )
    return packet, storyboard


def _profile(minimum: str = "5") -> CapabilityConstraintProfile:
    return CapabilityConstraintProfile(
        profile_id=OpaqueId("profile-1"),
        profile_sha256=HashDigest("b" * 64),
        capability_id=CapabilityId("cap-generate"),
        minimum_duration_seconds=Decimal(minimum),
        first_frame_aspect_behavior=FirstFrameAspectBehavior.MATCH_OUTPUT,
        unsupported_render_dependencies=frozenset({OpaqueId("screen-text")}),
    )


def test_all_generic_feasibility_checks_pass() -> None:
    packet, storyboard = _inputs()
    judgment = evaluate_generation_feasibility(
        packet,
        storyboard,
        _profile(),
        creator_role="role:writer",
        reviewer_role="role:reviewer",
    )
    assert judgment.overall is FeasibilityStatus.PASS
    assert len(judgment.checks) == 6
    document = feasibility_review_to_mapping(
        judgment,
        episode_id="ep-1",
        rules_version="rules-test",
        reviewed_at="2026-07-21T00:00:00Z",
    )
    assert validate_artifact(document).ok is True


def test_duration_aspect_and_before_state_fail_without_guessing() -> None:
    packet, storyboard = _inputs(duration=4, state="after", width=1280)
    judgment = evaluate_generation_feasibility(
        packet,
        storyboard,
        _profile("5"),
        creator_role="role:writer",
        reviewer_role="role:reviewer",
    )
    assert judgment.overall is FeasibilityStatus.FAIL
    failed = {item.kind.value for item in judgment.checks if item.status.value == "fail"}
    assert {
        "minimum_duration",
        "first_frame_aspect",
        "first_frame_before_state",
    } <= failed
