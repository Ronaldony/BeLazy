"""Pure generation-feasibility evaluation over declared packet facts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from video_factory.artifacts import validate_artifact_mapping
from video_factory.domain import OpaqueId, RoleId
from video_factory.engine.artifact_graph import (
    ArtifactSnapshot,
    artifact_reference_to_mapping,
)
from video_factory.providers import (
    CapabilityConstraintProfile,
    FirstFrameAspectBehavior,
)

from .contracts import (
    FeasibilityCheck,
    FeasibilityCheckKind,
    FeasibilityStatus,
    GenerationFeasibilityJudgment,
)


class GenerationFeasibilityError(ValueError):
    """Raised when feasibility cannot be evaluated without guessing."""


#: Packet versions accepted by strict feasibility evaluation.
STRICT_PACKET_VERSIONS = frozenset(
    {"generation-packet/2.0", "generation-packet/2.1"}
)

#: Packet versions that declare cross-shot state carryover. Older packets have
#: no field to express it, so the carryover check is not emitted for them and
#: their judgments keep their previous shape.
CARRYOVER_PACKET_VERSIONS = frozenset({"generation-packet/2.1"})


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise GenerationFeasibilityError(f"{label} must be an object")
    return value


def _sequence(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(
        value, (str, bytes, bytearray)
    ):
        raise GenerationFeasibilityError(f"{label} must be an array")
    return value


def _check(
    kind: FeasibilityCheckKind,
    shot_id: str,
    status: FeasibilityStatus,
    message: str,
) -> FeasibilityCheck:
    return FeasibilityCheck(
        check_id=OpaqueId(f"{shot_id}:{kind.value}"),
        kind=kind,
        shot_id=OpaqueId(shot_id),
        status=status,
        message=message,
    )


def _ratio_matches(
    left_width: object,
    left_height: object,
    right_width: object,
    right_height: object,
) -> bool | None:
    values = (left_width, left_height, right_width, right_height)
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value <= 0
        for value in values
    ):
        return None
    return left_width * right_height == right_width * left_height


def evaluate_generation_feasibility(
    packet: ArtifactSnapshot,
    storyboard: ArtifactSnapshot,
    profile: CapabilityConstraintProfile,
    *,
    creator_role: str,
    reviewer_role: str,
) -> GenerationFeasibilityJudgment:
    """Evaluate generic feasibility facts without calling a media provider."""

    for label, snapshot in (("packet", packet), ("storyboard", storyboard)):
        validation = validate_artifact_mapping(snapshot.document)
        if not validation.ok:
            raise GenerationFeasibilityError(
                f"{label} schema validation failed: "
                + "; ".join(validation.error_texts)
            )
    if packet.artifact_version not in STRICT_PACKET_VERSIONS:
        raise GenerationFeasibilityError(
            "strict feasibility requires one of: "
            + ", ".join(sorted(STRICT_PACKET_VERSIONS))
        )
    if packet.episode_id != storyboard.episode_id:
        raise GenerationFeasibilityError("packet and storyboard episode ids differ")
    if packet.rules_version != storyboard.rules_version:
        raise GenerationFeasibilityError("packet and storyboard rules versions differ")
    if not creator_role or not reviewer_role:
        raise GenerationFeasibilityError("creator and reviewer roles must be non-empty")
    if creator_role == reviewer_role:
        raise GenerationFeasibilityError("creator and reviewer roles must differ")

    document = packet.document
    storyboard_ref = document.get("storyboard_ref")
    if not isinstance(storyboard_ref, Mapping) or not _reference_matches(
        storyboard, storyboard_ref
    ):
        raise GenerationFeasibilityError(
            "packet storyboard_ref is not bound to the current storyboard"
        )

    output = _mapping(document.get("output"), "packet.output")
    target = _mapping(output.get("target"), "packet.output.target")
    target_width = target.get("width")
    target_height = target.get("height")
    shots = _sequence(document.get("shots"), "packet.shots")
    if not shots:
        raise GenerationFeasibilityError("packet.shots must not be empty")

    storyboard_shots = _sequence(
        storyboard.document.get("shots"), "storyboard.shots"
    )
    storyboard_by_id: dict[str, Mapping[str, object]] = {}
    for index, raw_storyboard_shot in enumerate(storyboard_shots):
        storyboard_shot = _mapping(
            raw_storyboard_shot, f"storyboard.shots[{index}]"
        )
        storyboard_shot_id = storyboard_shot.get("shot_id")
        if not isinstance(storyboard_shot_id, str) or not storyboard_shot_id:
            raise GenerationFeasibilityError(
                f"storyboard.shots[{index}].shot_id must be non-empty"
            )
        if storyboard_shot_id in storyboard_by_id:
            raise GenerationFeasibilityError(
                f"duplicate storyboard shot_id: {storyboard_shot_id}"
            )
        storyboard_by_id[storyboard_shot_id] = storyboard_shot

    # Declared packet shot order proves carryover direction and must itself be
    # unambiguous for the standalone strict-feasibility API.
    shot_order: dict[str, int] = {}
    for index, raw_shot in enumerate(shots):
        if isinstance(raw_shot, Mapping):
            candidate = raw_shot.get("shot_id")
            if isinstance(candidate, str) and candidate:
                if candidate in shot_order:
                    raise GenerationFeasibilityError(
                        f"duplicate packet shot_id: {candidate}"
                    )
                shot_order[candidate] = index

    carryover_declared = packet.artifact_version in CARRYOVER_PACKET_VERSIONS

    checks: list[FeasibilityCheck] = []
    for index, raw_shot in enumerate(shots):
        shot = _mapping(raw_shot, f"packet.shots[{index}]")
        shot_id = shot.get("shot_id")
        if not isinstance(shot_id, str) or not shot_id:
            raise GenerationFeasibilityError(
                f"packet.shots[{index}].shot_id must be non-empty"
            )

        capability_id = shot.get("capability_id")
        capability_ok = capability_id == str(profile.capability_id)
        checks.append(
            _check(
                FeasibilityCheckKind.CAPABILITY_BINDING,
                shot_id,
                FeasibilityStatus.PASS if capability_ok else FeasibilityStatus.FAIL,
                (
                    "shot capability matches the injected profile"
                    if capability_ok
                    else "shot capability does not match the injected profile"
                ),
            )
        )

        duration = shot.get("duration_sec")
        if profile.minimum_duration_seconds is None:
            duration_status = FeasibilityStatus.INCONCLUSIVE
            duration_message = "capability minimum duration is unknown"
        elif not isinstance(duration, (int, float, Decimal)) or isinstance(
            duration, bool
        ):
            duration_status = FeasibilityStatus.INCONCLUSIVE
            duration_message = "shot duration is not numeric"
        elif Decimal(str(duration)) >= profile.minimum_duration_seconds:
            duration_status = FeasibilityStatus.PASS
            duration_message = "shot duration satisfies the capability minimum"
        else:
            duration_status = FeasibilityStatus.FAIL
            duration_message = "shot duration is below the capability minimum"
        checks.append(
            _check(
                FeasibilityCheckKind.MINIMUM_DURATION,
                shot_id,
                duration_status,
                duration_message,
            )
        )

        first_frame = _mapping(
            shot.get("first_frame"), f"packet.shots[{index}].first_frame"
        )
        if (
            profile.first_frame_aspect_behavior
            is FirstFrameAspectBehavior.MATCH_OUTPUT
        ):
            ratio_match = _ratio_matches(
                first_frame.get("width"),
                first_frame.get("height"),
                target_width,
                target_height,
            )
            if ratio_match is True:
                aspect_status = FeasibilityStatus.PASS
                aspect_message = "first-frame aspect matches output target"
            elif ratio_match is False:
                aspect_status = FeasibilityStatus.FAIL
                aspect_message = "first-frame aspect differs from output target"
            else:
                aspect_status = FeasibilityStatus.INCONCLUSIVE
                aspect_message = "first-frame or target dimensions are unavailable"
        elif profile.first_frame_aspect_behavior is FirstFrameAspectBehavior.FREE:
            aspect_status = FeasibilityStatus.PASS
            aspect_message = "capability does not require matching first-frame aspect"
        else:
            aspect_status = FeasibilityStatus.INCONCLUSIVE
            aspect_message = "capability first-frame aspect behavior is unknown"
        checks.append(
            _check(
                FeasibilityCheckKind.FIRST_FRAME_ASPECT,
                shot_id,
                aspect_status,
                aspect_message,
            )
        )

        before = first_frame.get("state") == "before"
        checks.append(
            _check(
                FeasibilityCheckKind.FIRST_FRAME_BEFORE_STATE,
                shot_id,
                FeasibilityStatus.PASS if before else FeasibilityStatus.FAIL,
                (
                    "first frame declares the pre-action state"
                    if before
                    else "first frame is not declared as the pre-action state"
                ),
            )
        )

        continuity = _mapping(
            shot.get("continuity"), f"packet.shots[{index}].continuity"
        )
        continuity_required = continuity.get("required") is True
        anchor = continuity.get("master_plate_ref")
        group_id = continuity.get("group_id")
        continuity_ok = (
            not continuity_required
            or (
                isinstance(anchor, Mapping)
                and isinstance(group_id, str)
                and bool(group_id)
            )
        )
        checks.append(
            _check(
                FeasibilityCheckKind.CONTINUITY_ANCHOR,
                shot_id,
                FeasibilityStatus.PASS
                if continuity_ok
                else FeasibilityStatus.FAIL,
                (
                    "continuity anchor is present or not required"
                    if continuity_ok
                    else "required cross-shot continuity lacks a master plate"
                ),
            )
        )

        if carryover_declared:
            carried = _sequence(
                continuity.get("carried_elements", ()),
                f"packet.shots[{index}].continuity.carried_elements",
            )
            depicted = {
                value
                for value in _sequence(
                    first_frame.get("depicted_elements", ()),
                    f"packet.shots[{index}].first_frame.depicted_elements",
                )
                if isinstance(value, str)
            }
            problems: list[str] = []
            declared_pairs: set[tuple[str, str]] = set()
            for entry_index, raw_entry in enumerate(carried):
                entry = _mapping(
                    raw_entry,
                    f"packet.shots[{index}].continuity."
                    f"carried_elements[{entry_index}]",
                )
                element_id = entry.get("element_id")
                from_shot_id = entry.get("from_shot_id")
                if not isinstance(element_id, str) or not element_id:
                    problems.append(f"[{entry_index}]: element_id is empty")
                    continue
                if (
                    not isinstance(from_shot_id, str)
                    or from_shot_id not in shot_order
                ):
                    problems.append(
                        f"{element_id}: source shot is not declared in this packet"
                    )
                    continue
                if shot_order[from_shot_id] >= index:
                    problems.append(
                        f"{element_id}: source shot {from_shot_id} does not "
                        "precede this shot"
                    )
                    continue
                declared_pairs.add((element_id, from_shot_id))
                if element_id not in depicted:
                    problems.append(
                        f"{element_id}: not depicted in this first frame"
                    )

            evidence_problem: str | None = None
            expected_pairs: set[tuple[str, str]] = set()
            if index > 0:
                previous_packet_shot = _mapping(
                    shots[index - 1], f"packet.shots[{index - 1}]"
                )
                previous_packet_shot_id = previous_packet_shot.get("shot_id")
                previous_source_shot_id = previous_packet_shot.get(
                    "source_shot_id"
                )
                source_storyboard_shot = storyboard_by_id.get(
                    previous_source_shot_id
                    if isinstance(previous_source_shot_id, str)
                    else ""
                )
                raw_expected = (
                    source_storyboard_shot.get("end_state_elements")
                    if source_storyboard_shot is not None
                    else None
                )
                if not isinstance(raw_expected, Sequence) or isinstance(
                    raw_expected, (str, bytes, bytearray)
                ):
                    evidence_problem = (
                        "the hash-bound storyboard source shot does not declare "
                        "end_state_elements"
                    )
                elif isinstance(previous_packet_shot_id, str):
                    expected_pairs = {
                        (element_id, previous_packet_shot_id)
                        for element_id in raw_expected
                        if isinstance(element_id, str)
                    }

            if evidence_problem is None:
                missing = expected_pairs - declared_pairs
                unexpected = declared_pairs - expected_pairs
                problems.extend(
                    f"{element_id}: required by the previous storyboard end state "
                    "but omitted from carried_elements"
                    for element_id, _source in sorted(missing)
                )
                problems.extend(
                    f"{element_id}: carried from {source_id} but not required by "
                    "the previous storyboard end state"
                    for element_id, source_id in sorted(unexpected)
                )

            if problems:
                carryover_status = FeasibilityStatus.FAIL
                carryover_message = (
                    "declared carried state is not complete or not honored by "
                    "this first frame: " + "; ".join(problems)
                )
            elif evidence_problem is not None:
                carryover_status = FeasibilityStatus.INCONCLUSIVE
                carryover_message = evidence_problem
            elif not carried:
                carryover_status = FeasibilityStatus.PASS
                carryover_message = (
                    "the hash-bound storyboard declares no state to carry into "
                    "this first frame"
                )
            else:
                carryover_status = FeasibilityStatus.PASS
                carryover_message = (
                    "first frame depicts the complete state required by the "
                    "hash-bound storyboard"
                )
            checks.append(
                _check(
                    FeasibilityCheckKind.FIRST_FRAME_STATE_CARRYOVER,
                    shot_id,
                    carryover_status,
                    carryover_message,
                )
            )

        dependencies = _sequence(
            shot.get("render_dependencies", ()),
            f"packet.shots[{index}].render_dependencies",
        )
        unsupported: list[str] = []
        for dep_index, raw_dependency in enumerate(dependencies):
            dependency = _mapping(
                raw_dependency,
                f"packet.shots[{index}].render_dependencies[{dep_index}]",
            )
            feature_id = dependency.get("feature_id")
            handled_in = dependency.get("handled_in")
            if (
                isinstance(feature_id, str)
                and handled_in == "generation"
                and OpaqueId(feature_id) in profile.unsupported_render_dependencies
            ):
                unsupported.append(feature_id)
        dependency_ok = not unsupported
        checks.append(
            _check(
                FeasibilityCheckKind.UNSUPPORTED_RENDER_DEPENDENCY,
                shot_id,
                FeasibilityStatus.PASS
                if dependency_ok
                else FeasibilityStatus.FAIL,
                (
                    "no unsupported required generation dependency"
                    if dependency_ok
                    else "unsupported generation dependencies: "
                    + ", ".join(sorted(unsupported))
                ),
            )
        )

    statuses = {check.status for check in checks}
    if FeasibilityStatus.FAIL in statuses:
        overall = FeasibilityStatus.FAIL
    elif FeasibilityStatus.INCONCLUSIVE in statuses:
        overall = FeasibilityStatus.INCONCLUSIVE
    else:
        overall = FeasibilityStatus.PASS

    return GenerationFeasibilityJudgment(
        packet=packet.reference,
        storyboard=storyboard.reference,
        capability_profile=profile,
        creator_role=RoleId(creator_role),
        reviewer_role=RoleId(reviewer_role),
        checks=tuple(checks),
        overall=overall,
    )


def _reference_matches(
    snapshot: ArtifactSnapshot,
    value: Mapping[str, object],
) -> bool:
    return (
        value.get("path") == str(snapshot.path)
        and value.get("sha256") == str(snapshot.sha256)
        and value.get("artifact_version") == snapshot.artifact_version
    )


def feasibility_review_to_mapping(
    judgment: GenerationFeasibilityJudgment,
    *,
    episode_id: str,
    rules_version: str,
    reviewed_at: str,
) -> dict[str, object]:
    """Serialize computed evidence; caller supplies the audit timestamp."""

    return {
        "artifact_version": "generation-feasibility-review/1.0",
        "rules_version": rules_version,
        "episode_id": episode_id,
        "subject": artifact_reference_to_mapping(judgment.packet),
        "storyboard_ref": artifact_reference_to_mapping(judgment.storyboard),
        "capability_profile": {
            "profile_id": str(judgment.capability_profile.profile_id),
            "sha256": str(judgment.capability_profile.profile_sha256),
            "capability_id": str(judgment.capability_profile.capability_id),
        },
        "creator_role": str(judgment.creator_role),
        "reviewer_role": str(judgment.reviewer_role),
        "reviewed_at": reviewed_at,
        "checks": [
            {
                "check_id": str(check.check_id),
                "kind": check.kind.value,
                "shot_id": str(check.shot_id),
                "status": check.status.value,
                "message": check.message,
            }
            for check in judgment.checks
        ],
        "verdict": judgment.overall.value,
    }
