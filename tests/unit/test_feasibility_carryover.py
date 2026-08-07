"""Cross-shot first-frame state carryover feasibility check.

A shot may declare that its first frame inherits state produced by an earlier
shot. The core proves the declarations are mutually consistent; it never looks
at pixels and never invents element vocabulary.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from video_factory.artifacts import validate_artifact
from video_factory.domain import CapabilityId, HashDigest, OpaqueId
from video_factory.engine import (
    artifact_reference_to_mapping,
    make_artifact_snapshot,
)
from video_factory.feasibility import (
    FeasibilityCheckKind,
    FeasibilityStatus,
    GenerationFeasibilityError,
    evaluate_generation_feasibility,
    feasibility_review_to_mapping,
)
from video_factory.providers import (
    CapabilityConstraintProfile,
    FirstFrameAspectBehavior,
)


def _storyboard(
    end_state_elements: dict[str, list[str]] | None = None,
) -> object:
    declared = (
        {"shot-1": ["prop-a"], "shot-2": []}
        if end_state_elements is None
        else end_state_elements
    )
    shots: list[dict[str, object]] = []
    for n in (1, 2):
        shot_id = f"shot-{n}"
        shot: dict[str, object] = {
            "shot_id": shot_id,
            "duration_sec": 5,
            "narrative_role": "hook" if n == 1 else "payoff",
            "characters": [],
            "action": "One action.",
            "creative_direction": "Plain.",
            "start_state": "before",
            "end_state": "after",
        }
        if shot_id in declared:
            shot["end_state_elements"] = list(declared[shot_id])
        shots.append(shot)
    return make_artifact_snapshot(
        "02_storyboard/storyboard.json",
        {
            "artifact_version": "storyboard/1.0",
            "rules_version": "rules-test",
            "episode_id": "ep-1",
            "generated_by": "role:writer",
            "premise": "test",
            "shots": shots,
        },
    )


def _shot(
    shot_id: str,
    *,
    depicted: list[str] | None = None,
    carried: list[dict[str, str]] | None = None,
    version_21: bool = True,
) -> dict[str, object]:
    first_frame: dict[str, object] = {
        "path": f"05_references/{shot_id}.png",
        "sha256": "a" * 64,
        "width": 720,
        "height": 1280,
        "state": "before",
    }
    continuity: dict[str, object] = {"required": False}
    if version_21:
        first_frame["depicted_elements"] = list(depicted or [])
        continuity["carried_elements"] = list(carried or [])
    return {
        "shot_id": shot_id,
        "source_shot_id": shot_id,
        "duration_sec": 5,
        "candidates": 1,
        "capability_id": "cap-generate",
        "prompt": "One action.",
        "first_frame": first_frame,
        "continuity": continuity,
        "render_dependencies": [
            {"feature_id": "screen-text", "handled_in": "edit"}
        ],
    }


def _packet(
    shots: list[dict[str, object]],
    *,
    version_21: bool = True,
    end_state_elements: dict[str, list[str]] | None = None,
) -> object:
    storyboard = _storyboard(end_state_elements)
    return (
        make_artifact_snapshot(
            "04_prompts/packet.json",
            {
                "artifact_version": (
                    "generation-packet/2.1"
                    if version_21
                    else "generation-packet/2.0"
                ),
                "rules_version": "rules-test",
                "episode_id": "ep-1",
                "generated_by": "role:writer",
                "approved_by_human": False,
                "storyboard_ref": artifact_reference_to_mapping(
                    storyboard.reference
                ),
                "output": {
                    "dir": "06_generated",
                    "filename_pattern": "{shot}.mp4",
                    "target": {"width": 720, "height": 1280},
                },
                "shots": shots,
            },
        ),
        storyboard,
    )


def _profile() -> CapabilityConstraintProfile:
    return CapabilityConstraintProfile(
        profile_id=OpaqueId("profile-1"),
        profile_sha256=HashDigest("b" * 64),
        capability_id=CapabilityId("cap-generate"),
        minimum_duration_seconds=Decimal("5"),
        first_frame_aspect_behavior=FirstFrameAspectBehavior.MATCH_OUTPUT,
        unsupported_render_dependencies=frozenset({OpaqueId("screen-text")}),
    )


def _judge(
    shots: list[dict[str, object]],
    *,
    version_21: bool = True,
    end_state_elements: dict[str, list[str]] | None = None,
):
    packet, storyboard = _packet(
        shots,
        version_21=version_21,
        end_state_elements=end_state_elements,
    )
    return evaluate_generation_feasibility(
        packet,
        storyboard,
        _profile(),
        creator_role="role:writer",
        reviewer_role="role:reviewer",
    )


def _carryover(judgment, shot_id: str):
    for check in judgment.checks:
        if (
            check.kind is FeasibilityCheckKind.FIRST_FRAME_STATE_CARRYOVER
            and str(check.shot_id) == shot_id
        ):
            return check
    return None


def test_packet_2_0_emits_no_carryover_check() -> None:
    """Older packets cannot declare carryover, so their judgment is unchanged."""

    judgment = _judge(
        [
            _shot("shot-1", version_21=False),
            _shot("shot-2", version_21=False),
        ],
        version_21=False,
    )
    assert _carryover(judgment, "shot-1") is None
    assert _carryover(judgment, "shot-2") is None
    assert judgment.overall is FeasibilityStatus.PASS


def test_empty_carried_elements_cannot_omit_storyboard_expected_state() -> None:
    judgment = _judge([_shot("shot-1"), _shot("shot-2")])
    check = _carryover(judgment, "shot-2")
    assert check is not None
    assert check.status is FeasibilityStatus.FAIL
    assert "omitted from carried_elements" in check.message
    assert judgment.overall is FeasibilityStatus.FAIL


def test_empty_carried_elements_passes_with_hash_bound_empty_evidence() -> None:
    judgment = _judge(
        [_shot("shot-1"), _shot("shot-2")],
        end_state_elements={"shot-1": [], "shot-2": []},
    )
    check = _carryover(judgment, "shot-2")
    assert check is not None
    assert check.status is FeasibilityStatus.PASS
    assert "storyboard declares no state" in check.message
    assert judgment.overall is FeasibilityStatus.PASS


def test_missing_storyboard_end_state_evidence_is_inconclusive() -> None:
    judgment = _judge(
        [_shot("shot-1"), _shot("shot-2")],
        end_state_elements={},
    )
    check = _carryover(judgment, "shot-2")
    assert check is not None
    assert check.status is FeasibilityStatus.INCONCLUSIVE
    assert "does not declare end_state_elements" in check.message
    assert judgment.overall is FeasibilityStatus.INCONCLUSIVE


def test_depicted_carried_element_passes() -> None:
    judgment = _judge(
        [
            _shot("shot-1", depicted=["prop-a"]),
            _shot(
                "shot-2",
                depicted=["prop-a"],
                carried=[{"element_id": "prop-a", "from_shot_id": "shot-1"}],
            ),
        ]
    )
    check = _carryover(judgment, "shot-2")
    assert check is not None
    assert check.status is FeasibilityStatus.PASS
    assert judgment.overall is FeasibilityStatus.PASS


def test_element_missing_from_next_first_frame_fails() -> None:
    """The EP001 defect: a prop attached in one shot is gone from the next."""

    judgment = _judge(
        [
            _shot("shot-1", depicted=["prop-a"]),
            _shot(
                "shot-2",
                depicted=[],
                carried=[{"element_id": "prop-a", "from_shot_id": "shot-1"}],
            ),
        ]
    )
    check = _carryover(judgment, "shot-2")
    assert check is not None
    assert check.status is FeasibilityStatus.FAIL
    assert "prop-a: not depicted in this first frame" in check.message
    assert judgment.overall is FeasibilityStatus.FAIL


def test_unknown_source_shot_fails() -> None:
    judgment = _judge(
        [
            _shot("shot-1"),
            _shot(
                "shot-2",
                depicted=["prop-a"],
                carried=[{"element_id": "prop-a", "from_shot_id": "shot-9"}],
            ),
        ]
    )
    check = _carryover(judgment, "shot-2")
    assert check is not None
    assert check.status is FeasibilityStatus.FAIL
    assert "source shot is not declared in this packet" in check.message


def test_source_shot_must_precede_the_inheriting_shot() -> None:
    judgment = _judge(
        [
            _shot(
                "shot-1",
                depicted=["prop-a"],
                carried=[{"element_id": "prop-a", "from_shot_id": "shot-2"}],
            ),
            _shot("shot-2", depicted=["prop-a"]),
        ]
    )
    check = _carryover(judgment, "shot-1")
    assert check is not None
    assert check.status is FeasibilityStatus.FAIL
    assert "does not precede this shot" in check.message


def test_serialized_review_with_carryover_check_validates() -> None:
    """The review schema must accept the new check kind, not just the dataclass."""

    judgment = _judge(
        [
            _shot("shot-1", depicted=["prop-a"]),
            _shot(
                "shot-2",
                depicted=["prop-a"],
                carried=[{"element_id": "prop-a", "from_shot_id": "shot-1"}],
            ),
        ]
    )
    document = feasibility_review_to_mapping(
        judgment,
        episode_id="ep-1",
        rules_version="rules-test",
        reviewed_at="2026-07-28T06:00:00Z",
    )
    kinds = {check["kind"] for check in document["checks"]}
    assert "first_frame_state_carryover" in kinds
    result = validate_artifact(document)
    assert result.ok, result.error_texts


def test_self_reference_fails() -> None:
    judgment = _judge(
        [
            _shot(
                "shot-1",
                depicted=["prop-a"],
                carried=[{"element_id": "prop-a", "from_shot_id": "shot-1"}],
            )
        ]
    )
    check = _carryover(judgment, "shot-1")
    assert check is not None
    assert check.status is FeasibilityStatus.FAIL
    assert "does not precede this shot" in check.message


def test_duplicate_packet_shot_id_is_rejected_by_strict_feasibility() -> None:
    duplicated = [
        _shot("shot-1"),
        _shot("shot-1"),
    ]
    packet, storyboard = _packet(
        duplicated,
        end_state_elements={"shot-1": []},
    )
    with pytest.raises(GenerationFeasibilityError, match="duplicate packet shot_id"):
        evaluate_generation_feasibility(
            packet,
            storyboard,
            _profile(),
            creator_role="role:writer",
            reviewer_role="role:reviewer",
        )
