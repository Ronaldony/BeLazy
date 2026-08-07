"""Hash-bound orchestration observation and next-step planning."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from video_factory.artifacts import validate_artifact
from video_factory.approvals import GateContext, gate_context_to_mapping
from video_factory.domain import HashDigest
from video_factory.engine import (
    ArtifactSnapshot,
    OrchestrationPlanError,
    artifact_reference_to_mapping,
    build_generation_readiness as _build_generation_readiness,
    make_artifact_snapshot,
    next_step_to_mapping,
    observe_episode_state,
    plan_next_step as _plan_next_step,
)


RULES = "rules-test"
EPISODE = "ep-synth"
HASH_A = "a" * 64
HASH_B = "b" * 64
GATE_CONTEXT = GateContext(
    workflow_definition_sha256=HashDigest("1" * 64),
    policy_bundle_sha256=HashDigest("2" * 64),
    rules_bundle_sha256=HashDigest("3" * 64),
    effective_config_sha256=HashDigest(HASH_B),
    current_manifest_sha256=HashDigest("4" * 64),
    evidence_graph_sha256=HashDigest("5" * 64),
    executable_plan_sha256=HashDigest("6" * 64),
)
EVALUATED_AT = datetime(2026, 7, 21, 4, 45, tzinfo=timezone.utc)
CONTEXT_FIELDS = (
    "workflow_definition_sha256",
    "policy_bundle_sha256",
    "rules_bundle_sha256",
    "effective_config_sha256",
    "current_manifest_sha256",
    "evidence_graph_sha256",
    "executable_plan_sha256",
)


def plan_next_step(observation, workflow_mode):
    return _plan_next_step(
        observation,
        workflow_mode,
        current_context=GATE_CONTEXT,
        evaluated_at=EVALUATED_AT,
    )


def build_generation_readiness(observation, workflow_mode):
    return _build_generation_readiness(
        observation,
        workflow_mode,
        current_context=GATE_CONTEXT,
        evaluated_at=EVALUATED_AT,
    )


def _snapshot(path: str, document: dict[str, object]) -> ArtifactSnapshot:
    return make_artifact_snapshot(path, document)


def _brief() -> ArtifactSnapshot:
    return _snapshot(
        "01_brief/brief.json",
        {
            "artifact_version": "brief/1.0",
            "rules_version": RULES,
            "episode_id": EPISODE,
            "summary": "A worker tests a strange device.",
            "hook": "The first result is impossible.",
            "development": "The constraints escalate.",
            "ending": "The device technically succeeds.",
            "risks": ["continuity"],
        },
    )


def _storyboard() -> ArtifactSnapshot:
    return _snapshot(
        "02_storyboard/storyboard.json",
        {
            "artifact_version": "storyboard/1.0",
            "rules_version": RULES,
            "episode_id": EPISODE,
            "generated_by": "role:writer",
            "premise": "A strange device demonstration.",
            "shots": [
                {
                    "shot_id": "shot-01",
                    "duration_sec": 5,
                    "narrative_role": "hook",
                    "characters": [],
                    "location": "lab",
                    "camera": {"framing": "wide", "movement": "static"},
                    "action": "Worker activates the device.",
                    "creative_direction": "Play it deadpan.",
                    "start_state": "device idle",
                    "end_state": "device active",
                    "end_state_elements": [],
                }
            ],
        },
    )


def _review(
    path: str,
    family: str,
    subject: ArtifactSnapshot,
    *,
    reviewer: str,
    verdict: str = "pass",
) -> ArtifactSnapshot:
    return _snapshot(
        path,
        {
            "artifact_version": f"{family}/1.0",
            "rules_version": RULES,
            "episode_id": EPISODE,
            "subject": artifact_reference_to_mapping(subject.reference),
            "creator_role": subject.document.get("generated_by")
            or subject.document.get("created_by"),
            "reviewer_role": reviewer,
            "reviewed_at": "2026-07-21T00:00:00Z",
            "verdict": verdict,
            "findings": [],
        },
    )


def _approval(
    path: str,
    family: str,
    capability: str,
    bound: tuple[ArtifactSnapshot, ...],
) -> ArtifactSnapshot:
    return _snapshot(
        path,
        {
            "artifact_version": f"{family}/2.0",
            "rules_version": RULES,
            "episode_id": EPISODE,
            "requirement_id": f"req-{family}",
            "capability_id": capability,
            "evidence_id": f"evidence-{family}",
            "state": "granted",
            "approved_by_human": True,
            "approver_role": "human:operator",
            "approved_at": "2026-07-21T01:00:00Z",
            "record_sha256": HASH_A,
            "bound_artifacts": [
                artifact_reference_to_mapping(item.reference) for item in bound
            ],
            "effective_config_sha256": HASH_B,
            "gate_context": gate_context_to_mapping(GATE_CONTEXT),
            "expires_at": "2026-07-21T06:00:00Z",
        },
    )


def _packet(
    storyboard: ArtifactSnapshot,
    *,
    packet_version: str = "generation-packet/2.0",
) -> ArtifactSnapshot:
    first_frame: dict[str, object] = {
        "path": "05_references/shot-01-before.png",
        "sha256": HASH_A,
        "width": 720,
        "height": 1280,
        "state": "before",
    }
    continuity: dict[str, object] = {
        "required": True,
        "group_id": "lab-set",
        "master_plate_ref": {
            "path": "05_references/lab-master.png",
            "sha256": HASH_B,
            "artifact_version": "reference-image/1.0",
        },
    }
    if packet_version == "generation-packet/2.1":
        first_frame["depicted_elements"] = []
        continuity["carried_elements"] = []
    return _snapshot(
        "04_prompts/generation_packet.json",
        {
            "artifact_version": packet_version,
            "rules_version": RULES,
            "episode_id": EPISODE,
            "generated_by": "role:writer",
            "approved_by_human": True,
            "storyboard_ref": artifact_reference_to_mapping(storyboard.reference),
            "output": {
                "dir": "06_generated",
                "filename_pattern": "{episode}_{shot}_{version}.mp4",
                "target": {"width": 720, "height": 1280},
            },
            "shots": [
                {
                    "shot_id": "shot-01",
                    "source_shot_id": "shot-01",
                    "duration_sec": 5,
                    "candidates": 2,
                    "capability_id": "cap.media.generate",
                    "prompt": "Worker activates one device.",
                    "first_frame": first_frame,
                    "continuity": continuity,
                    "render_dependencies": [
                        {"feature_id": "screen-text", "handled_in": "edit"}
                    ],
                }
            ],
        },
    )


def _feasibility(
    packet: ArtifactSnapshot,
    storyboard: ArtifactSnapshot,
    *,
    verdict: str = "pass",
) -> ArtifactSnapshot:
    kinds = (
        "capability_binding",
        "minimum_duration",
        "first_frame_aspect",
        "first_frame_before_state",
        "continuity_anchor",
        *(
            ("first_frame_state_carryover",)
            if packet.artifact_version == "generation-packet/2.1"
            else ()
        ),
        "unsupported_render_dependency",
    )
    return _snapshot(
        "04_prompts/generation_feasibility.json",
        {
            "artifact_version": "generation-feasibility-review/1.0",
            "rules_version": RULES,
            "episode_id": EPISODE,
            "subject": artifact_reference_to_mapping(packet.reference),
            "storyboard_ref": artifact_reference_to_mapping(storyboard.reference),
            "capability_profile": {
                "profile_id": "profile-media",
                "sha256": HASH_A,
                "capability_id": "cap.media.generate",
            },
            "creator_role": "role:writer",
            "reviewer_role": "role:feasibility-reviewer",
            "reviewed_at": "2026-07-21T02:00:00Z",
            "checks": [
                {
                    "check_id": f"{shot['shot_id']}:{kind}",
                    "kind": kind,
                    "shot_id": shot["shot_id"],
                    "status": "pass" if verdict == "pass" else "fail",
                    "message": "evaluated from injected capability facts",
                }
                for shot in packet.document["shots"]
                for kind in kinds
            ],
            "verdict": verdict,
        },
    )


def _generation_gate_snapshots(
    *,
    packet_review_verdict: str = "pass",
    include_packet_approval: bool = True,
    packet_version: str = "generation-packet/2.0",
) -> list[ArtifactSnapshot]:
    brief = _brief()
    storyboard = _storyboard()
    storyboard_review = _review(
        "02_storyboard/storyboard_review.json",
        "storyboard-review",
        storyboard,
        reviewer="role:storyboard-reviewer",
    )
    storyboard_approval = _approval(
        "02_storyboard/storyboard_approval.json",
        "storyboard-approval",
        "storyboard_approval",
        (storyboard, storyboard_review),
    )
    packet = _packet(storyboard, packet_version=packet_version)
    packet_review = _review(
        "04_prompts/packet_review.json",
        "packet-review",
        packet,
        reviewer="role:packet-reviewer",
        verdict=packet_review_verdict,
    )
    feasibility = _feasibility(packet, storyboard)
    snapshots = [
        brief,
        storyboard,
        storyboard_review,
        storyboard_approval,
        packet,
        packet_review,
        feasibility,
    ]
    if include_packet_approval:
        snapshots.append(
            _approval(
                "04_prompts/packet_approval.json",
                "packet-approval",
                "generation_approval",
                (packet, packet_review, feasibility),
            )
        )
    return snapshots


def _mutate_approval_context(
    snapshots: list[ArtifactSnapshot],
    family: str,
    field: str,
    value: str = "e" * 64,
) -> list[ArtifactSnapshot]:
    changed = list(snapshots)
    approval = next(item for item in changed if item.family == family)
    document = deepcopy(dict(approval.document))
    context = document.get("gate_context")
    assert isinstance(context, dict)
    context[field] = value
    if field == "effective_config_sha256":
        document["effective_config_sha256"] = value
    replacement = _snapshot(str(approval.path), document)
    changed[changed.index(approval)] = replacement
    return changed


def _two_shot_generation_gate_snapshots() -> list[ArtifactSnapshot]:
    storyboard_document = deepcopy(_storyboard().document)
    storyboard_document["shots"][0]["end_state_elements"] = ["element-1"]
    storyboard_document["shots"].append(
        {
            "shot_id": "shot-02",
            "duration_sec": 5,
            "narrative_role": "payoff",
            "characters": [],
            "location": "lab",
            "camera": {"framing": "wide", "movement": "static"},
            "action": "Worker observes the result.",
            "creative_direction": "Play it deadpan.",
            "start_state": "device active",
            "end_state": "device active",
            "end_state_elements": [],
        }
    )
    storyboard = _snapshot(
        "02_storyboard/storyboard.json", storyboard_document
    )
    storyboard_review = _review(
        "02_storyboard/storyboard_review.json",
        "storyboard-review",
        storyboard,
        reviewer="role:storyboard-reviewer",
    )
    storyboard_approval = _approval(
        "02_storyboard/storyboard_approval.json",
        "storyboard-approval",
        "storyboard_approval",
        (storyboard, storyboard_review),
    )

    packet_document = deepcopy(
        _packet(
            storyboard, packet_version="generation-packet/2.1"
        ).document
    )
    second_shot = deepcopy(packet_document["shots"][0])
    second_shot["shot_id"] = "shot-02"
    second_shot["source_shot_id"] = "shot-02"
    second_shot["first_frame"]["path"] = "05_references/shot-02-before.png"
    second_shot["first_frame"]["depicted_elements"] = ["element-1"]
    second_shot["continuity"]["carried_elements"] = [
        {"element_id": "element-1", "from_shot_id": "shot-01"}
    ]
    packet_document["shots"].append(second_shot)
    packet = _snapshot("04_prompts/generation_packet.json", packet_document)
    packet_review = _review(
        "04_prompts/packet_review.json",
        "packet-review",
        packet,
        reviewer="role:packet-reviewer",
    )
    feasibility = _feasibility(packet, storyboard)
    packet_approval = _approval(
        "04_prompts/packet_approval.json",
        "packet-approval",
        "generation_approval",
        (packet, packet_review, feasibility),
    )
    return [
        _brief(),
        storyboard,
        storyboard_review,
        storyboard_approval,
        packet,
        packet_review,
        feasibility,
        packet_approval,
    ]


def _two_shot_qcs() -> list[ArtifactSnapshot]:
    qcs: list[ArtifactSnapshot] = []
    for index, shot_id in enumerate(("shot-01", "shot-02")):
        qcs.append(
            _snapshot(
                f"08_qc/{shot_id}.json",
                {
                    "artifact_version": "shot-qc/2.0",
                    "rules_version": RULES,
                    "episode_id": EPISODE,
                    "shot_id": shot_id,
                    "checked_at": "2026-07-28T08:00:00Z",
                    "subject": {
                        "path": f"06_generated/{shot_id}-v1.mp4",
                        "sha256": (HASH_A if index == 0 else HASH_B),
                        "artifact_version": "media-output/1.0",
                    },
                    "measurements": [],
                    "constraints": [],
                    "findings": [],
                    "verdict": "pass",
                },
            )
        )
    return qcs


def _continuity_qc(
    shot_qcs: list[ArtifactSnapshot],
    *,
    verdict: str = "pass",
    stale_subject: bool = False,
    evidence_failure: bool = False,
    element_id: str = "element-1",
) -> ArtifactSnapshot:
    axes = ("relative_scale", "orientation_shape", "presence")
    observations: list[dict[str, object]] = []
    comparisons: list[dict[str, object]] = []
    measurements: list[dict[str, object]] = []
    judgments: list[dict[str, object]] = []
    for axis in axes:
        left_id = f"obs-{axis}-left"
        right_id = f"obs-{axis}-right"
        for observation_id, shot_id, sample in (
            (left_id, "shot-01", "last"),
            (right_id, "shot-02", "first"),
        ):
            observations.append(
                {
                    "observation_id": observation_id,
                    "shot_id": shot_id,
                    "element_id": element_id,
                    "axis": axis,
                    "sample_point": sample,
                    "measure_method": None,
                    "measure_argv_hint": [],
                    "fallback_measure_methods": [],
                }
            )
        comparison: dict[str, object] = {
            "comparison_id": f"cmp-{axis}",
            "element_id": element_id,
            "axis": axis,
            "from_observation_id": left_id,
            "to_observation_id": right_id,
            "severity": "error",
        }
        if axis == "relative_scale":
            comparison["tolerance"] = "0.1"
            values = (1.0, 1.0)
        elif axis == "orientation_shape":
            values = ("same-shape", "same-shape")
        else:
            values = (True, True)
        comparisons.append(comparison)
        measurements.extend(
            [
                {"measurement_id": left_id, "value": values[0], "unit": None},
                {"measurement_id": right_id, "value": values[1], "unit": None},
            ]
        )
        judgments.append(
            {
                "comparison_id": f"cmp-{axis}",
                "status": "pass" if verdict == "pass" else "fail",
                "message": "synthetic continuity evidence",
            }
        )
    if evidence_failure:
        next(
            item
            for item in measurements
            if item["measurement_id"] == "obs-presence-right"
        )["value"] = False

    subjects: list[dict[str, object]] = []
    for index, qc in enumerate(shot_qcs):
        artifact = deepcopy(qc.document["subject"])
        if stale_subject and index == 1:
            artifact["sha256"] = HASH_A
        subjects.append(
            {
                "shot_id": qc.document["shot_id"],
                "artifact": artifact,
            }
        )
    return _snapshot(
        "08_qc/continuity.json",
        {
            "artifact_version": "continuity-qc/1.0",
            "rules_version": RULES,
            "episode_id": EPISODE,
            "checked_at": "2026-07-28T08:10:00Z",
            "subjects": subjects,
            "observations": observations,
            "comparisons": comparisons,
            "measurements": measurements,
            "judgments": judgments,
            "verdict": verdict,
        },
    )


def test_brief_only_requires_storyboard() -> None:
    observation = observe_episode_state([_brief()])
    plan = plan_next_step(observation, "standard")
    assert plan.action_type == "create_storyboard"
    assert plan.transition_applied is False
    assert plan.auto_execution is False


def test_raw_documents_are_rejected_instead_of_trusted() -> None:
    with pytest.raises(OrchestrationPlanError, match="snapshot envelope"):
        observe_episode_state(
            [
                {
                    "artifact_version": "brief/1.0",
                    "episode_id": EPISODE,
                    "rules_version": RULES,
                }
            ]
        )


def test_failed_packet_review_blocks_generation() -> None:
    observation = observe_episode_state(
        _generation_gate_snapshots(
            packet_review_verdict="fail",
            include_packet_approval=False,
        )
    )
    plan = plan_next_step(observation, "standard")
    assert plan.action_type == "review_or_revise_generation_packet"
    assert any("verdict" in blocker for blocker in plan.blockers)


def test_missing_packet_approval_is_the_only_generation_blocker() -> None:
    observation = observe_episode_state(
        _generation_gate_snapshots(include_packet_approval=False)
    )
    plan = plan_next_step(observation, "standard")
    assert plan.action_type == "approve_generation"
    assert plan.approval_required is True
    readiness = build_generation_readiness(observation, "standard")
    assert readiness.ready is False
    assert any("packet-approval" in item for item in readiness.blockers)


def test_generation_readiness_requires_all_bound_pass_evidence() -> None:
    observation = observe_episode_state(_generation_gate_snapshots())
    readiness = build_generation_readiness(observation, "standard")
    assert readiness.ready is True
    assert readiness.packet is not None
    assert readiness.feasibility_review is not None
    assert readiness.approval_evidence is not None
    plan = plan_next_step(observation, "standard")
    assert plan.action_type == "run_external_generation"
    assert "paid_external_generation" in plan.prohibited_actions


def test_generation_readiness_accepts_packet_2_1_end_to_end() -> None:
    snapshots = _generation_gate_snapshots(
        packet_version="generation-packet/2.1"
    )
    feasibility = next(
        item for item in snapshots if item.family == "generation-feasibility-review"
    )
    assert {
        check["kind"] for check in feasibility.document["checks"]
    } == {
        "capability_binding",
        "minimum_duration",
        "first_frame_aspect",
        "first_frame_before_state",
        "continuity_anchor",
        "first_frame_state_carryover",
        "unsupported_render_dependency",
    }
    observation = observe_episode_state(snapshots)
    readiness = build_generation_readiness(observation, "standard")
    assert readiness.ready is True
    assert readiness.blockers == ()
    assert plan_next_step(observation, "standard").action_type == (
        "run_external_generation"
    )


@pytest.mark.parametrize("field", CONTEXT_FIELDS)
@pytest.mark.parametrize(
    ("family", "expected_action"),
    [
        ("storyboard-approval", "approve_storyboard"),
        ("packet-approval", "approve_generation"),
    ],
)
def test_storyboard_and_generation_reject_every_context_digest_mismatch(
    field: str,
    family: str,
    expected_action: str,
) -> None:
    snapshots = _mutate_approval_context(
        _generation_gate_snapshots(), family, field
    )
    observation = observe_episode_state(snapshots)
    plan = _plan_next_step(
        observation,
        "standard",
        current_context=GATE_CONTEXT,
        evaluated_at=EVALUATED_AT,
    )
    assert plan.action_type == expected_action
    assert any("context" in blocker for blocker in plan.blockers)


def test_missing_expired_and_duplicate_approval_contexts_fail_closed() -> None:
    snapshots = _generation_gate_snapshots()
    packet_approval = next(
        item for item in snapshots if item.family == "packet-approval"
    )

    missing_context = _plan_next_step(
        observe_episode_state(snapshots),
        "standard",
        current_context=None,
        evaluated_at=EVALUATED_AT,
    )
    assert missing_context.action_type == "approve_storyboard"

    expired_document = deepcopy(dict(packet_approval.document))
    expired_document["expires_at"] = "2026-07-21T02:00:00Z"
    expired = _snapshot(str(packet_approval.path), expired_document)
    expired_snapshots = list(snapshots)
    expired_snapshots[expired_snapshots.index(packet_approval)] = expired
    expired_plan = plan_next_step(
        observe_episode_state(expired_snapshots), "standard"
    )
    assert expired_plan.action_type == "approve_generation"
    assert any("expired" in blocker for blocker in expired_plan.blockers)

    duplicate_document = deepcopy(dict(packet_approval.document))
    bound = duplicate_document["bound_artifacts"]
    assert isinstance(bound, list)
    bound.append(deepcopy(bound[0]))
    duplicate = _snapshot(str(packet_approval.path), duplicate_document)
    duplicate_snapshots = list(snapshots)
    duplicate_snapshots[duplicate_snapshots.index(packet_approval)] = duplicate
    duplicate_plan = plan_next_step(
        observe_episode_state(duplicate_snapshots), "standard"
    )
    assert duplicate_plan.action_type == "approve_generation"
    assert any("duplicate" in blocker for blocker in duplicate_plan.blockers)


def test_plan_identity_changes_when_material_context_changes() -> None:
    observation = observe_episode_state([_brief()])
    first = _plan_next_step(
        observation,
        "standard",
        current_context=GATE_CONTEXT,
        evaluated_at=EVALUATED_AT,
    )
    changed = GateContext(
        **{
            **gate_context_to_mapping(GATE_CONTEXT),
            "executable_plan_sha256": HashDigest("e" * 64),
        }
    )
    second = _plan_next_step(
        observation,
        "standard",
        current_context=changed,
        evaluated_at=EVALUATED_AT,
    )
    assert first.next_step_id != second.next_step_id
    assert first.gate_context_sha256 != second.gate_context_sha256


def test_multishot_pipeline_requires_current_continuity_qc() -> None:
    snapshots = _two_shot_generation_gate_snapshots()
    shot_qcs = _two_shot_qcs()
    observation = observe_episode_state([*snapshots, *shot_qcs])
    plan = plan_next_step(observation, "standard")
    assert plan.action_type == "run_continuity_qc"
    assert any("exactly one current" in blocker for blocker in plan.blockers)


def test_failed_continuity_qc_blocks_downstream_planning() -> None:
    snapshots = _two_shot_generation_gate_snapshots()
    shot_qcs = _two_shot_qcs()
    continuity_qc = _continuity_qc(shot_qcs, verdict="fail")
    observation = observe_episode_state(
        [*snapshots, *shot_qcs, continuity_qc]
    )
    plan = plan_next_step(observation, "standard")
    assert plan.action_type == "remediate_continuity_qc"
    assert any("verdict" in blocker for blocker in plan.blockers)


def test_stale_continuity_qc_subject_hash_blocks_downstream_planning() -> None:
    snapshots = _two_shot_generation_gate_snapshots()
    shot_qcs = _two_shot_qcs()
    continuity_qc = _continuity_qc(shot_qcs, stale_subject=True)
    observation = observe_episode_state(
        [*snapshots, *shot_qcs, continuity_qc]
    )
    plan = plan_next_step(observation, "standard")
    assert plan.action_type == "remediate_continuity_qc"
    assert any("hash-bind" in blocker for blocker in plan.blockers)


def test_continuity_qc_stored_pass_cannot_hide_failing_measurements() -> None:
    snapshots = _two_shot_generation_gate_snapshots()
    shot_qcs = _two_shot_qcs()
    continuity_qc = _continuity_qc(shot_qcs, evidence_failure=True)
    observation = observe_episode_state(
        [*snapshots, *shot_qcs, continuity_qc]
    )
    plan = plan_next_step(observation, "standard")
    assert plan.action_type == "remediate_continuity_qc"
    assert any("serialized evidence" in blocker for blocker in plan.blockers)


def test_continuity_qc_must_cover_declared_carried_elements() -> None:
    snapshots = _two_shot_generation_gate_snapshots()
    shot_qcs = _two_shot_qcs()
    continuity_qc = _continuity_qc(shot_qcs, element_id="different-element")
    observation = observe_episode_state(
        [*snapshots, *shot_qcs, continuity_qc]
    )
    plan = plan_next_step(observation, "standard")
    assert plan.action_type == "remediate_continuity_qc"
    assert any("carried element" in blocker for blocker in plan.blockers)


def test_passing_current_continuity_qc_unlocks_candidate_ranking() -> None:
    snapshots = _two_shot_generation_gate_snapshots()
    shot_qcs = _two_shot_qcs()
    continuity_qc = _continuity_qc(shot_qcs)
    observation = observe_episode_state(
        [*snapshots, *shot_qcs, continuity_qc]
    )
    plan = plan_next_step(observation, "standard")
    assert plan.action_type == "rank_generation_candidates"


def test_full_lineage_stops_at_ready_for_human_publish() -> None:
    snapshots = _generation_gate_snapshots()
    packet = next(item for item in snapshots if item.family == "generation-packet")
    brief = next(item for item in snapshots if item.family == "brief")
    shot_qc = _snapshot(
        "08_qc/shot-01.json",
        {
            "artifact_version": "shot-qc/2.0",
            "rules_version": RULES,
            "episode_id": EPISODE,
            "shot_id": "shot-01",
            "checked_at": "2026-07-21T03:00:00Z",
            "subject": {
                "path": "06_generated/shot-01-v1.mp4",
                "sha256": HASH_A,
                "artifact_version": "media-output/1.0",
            },
            "measurements": [],
            "constraints": [],
            "findings": [],
            "verdict": "pass",
        },
    )
    ranking = _snapshot(
        "08_qc/candidate_ranking.json",
        {
            "artifact_version": "candidate-ranking/1.0",
            "rules_version": RULES,
            "episode_id": EPISODE,
            "generated_at": "2026-07-21T03:10:00Z",
            "packet_ref": artifact_reference_to_mapping(packet.reference),
            "ranking_policy": ["warning_count asc"],
            "shots": [
                {
                    "shot_id": "shot-01",
                    "candidates": [
                        {
                            "rank": 1,
                            "file": "06_generated/shot-01-v1.mp4",
                            "adapter_id": "adapter-media",
                            "qc_ref": {
                                "path": str(shot_qc.path),
                                "sha256": str(shot_qc.sha256),
                            },
                            "media_sha256": HASH_A,
                            "metrics": {"warning_count": 0},
                        }
                    ],
                }
            ],
        },
    )
    edit_manifest = _snapshot(
        "07_edit/edit_manifest.json",
        {
            "artifact_version": "edit-manifest/1.0",
            "rules_version": RULES,
            "episode_id": EPISODE,
            "selected_by_human": True,
            "selected_by": "human:editor",
            "selected_at": "2026-07-21T03:20:00Z",
            "input_clips": [
                {
                    "shot_id": "shot-01",
                    "path": "06_generated/shot-01-v1.mp4",
                    "sha256": HASH_A,
                }
            ],
            "audio_required": True,
        },
    )
    rough_cut = _snapshot(
        "07_edit/rough_cut_report.json",
        {
            "artifact_version": "rough-cut-report/1.0",
            "rules_version": RULES,
            "episode_id": EPISODE,
            "checked_at": "2026-07-21T03:30:00Z",
            "status": "PASS",
            "ranking_ref": artifact_reference_to_mapping(ranking.reference),
            "inputs": [
                {
                    "shot_id": "shot-01",
                    "path": "06_generated/shot-01-v1.mp4",
                    "sha256": HASH_A,
                    "duration_sec": 5,
                }
            ],
            "output_path": "07_edit/rough_cut.mp4",
        },
    )
    final_qc = _snapshot(
        "08_qc/final.json",
        {
            "artifact_version": "shot-qc/2.0",
            "rules_version": RULES,
            "episode_id": EPISODE,
            "shot_id": "final-output",
            "checked_at": "2026-07-21T04:00:00Z",
            "subject": {
                "path": "07_edit/final.mp4",
                "sha256": HASH_B,
                "artifact_version": "media-output/1.0",
            },
            "measurements": [],
            "constraints": [],
            "findings": [],
            "verdict": "warn",
        },
    )
    delivery = _snapshot(
        "07_edit/final_delivery.json",
        {
            "artifact_version": "final-delivery/1.0",
            "rules_version": RULES,
            "episode_id": EPISODE,
            "created_by": "role:editor",
            "created_at": "2026-07-21T04:10:00Z",
            "selected_output": dict(final_qc.document["subject"]),
            "lineage_refs": [
                artifact_reference_to_mapping(edit_manifest.reference),
                artifact_reference_to_mapping(rough_cut.reference),
            ],
            "technical_qc_ref": artifact_reference_to_mapping(final_qc.reference),
            "technical_verdict": "warn",
        },
    )
    final_review = _review(
        "08_qc/final_review.json",
        "final-review",
        delivery,
        reviewer="role:final-reviewer",
    )
    metadata = _snapshot(
        "09_publish/metadata.json",
        {
            "artifact_version": "publish-metadata-draft/1.0",
            "rules_version": RULES,
            "episode_id": EPISODE,
            "drafted_at": "2026-07-21T04:20:00Z",
            "source_brief_ref": artifact_reference_to_mapping(brief.reference),
            "title": "Synthetic title",
            "description": "Synthetic description.",
            "tags": ["synthetic"],
            "human_review_required": True,
            "target_platforms": ["platform"],
        },
    )
    publish_approval = _snapshot(
        "09_publish/publish_approval.json",
        {
            "artifact_version": "publish-approval/1.0",
            "rules_version": RULES,
            "episode_id": EPISODE,
            "requirement_id": "req-publish",
            "capability_id": "publish_approval",
            "evidence_id": "evidence-publish",
            "state": "granted",
            "approved_by_human": True,
            "approver_role": "human:publisher",
            "approved_at": "2026-07-21T04:30:00Z",
            "record_sha256": HASH_A,
            "bound_artifacts": [
                artifact_reference_to_mapping(item.reference)
                for item in (delivery, final_review, metadata)
            ],
            "effective_config_sha256": HASH_B,
            "gate_context": gate_context_to_mapping(GATE_CONTEXT),
            "expires_at": "2026-07-21T05:00:00Z",
        },
    )
    observation = observe_episode_state(
        [
            *snapshots,
            shot_qc,
            ranking,
            edit_manifest,
            rough_cut,
            final_qc,
            delivery,
            final_review,
            metadata,
            publish_approval,
        ]
    )
    assert observation.findings == ()
    plan = plan_next_step(observation, "standard")
    assert plan.action_type == "ready_for_human_publish"
    assert plan.auto_execution is False
    assert "publish" in plan.prohibited_actions

    lineage_without_publish = [
        *snapshots,
        shot_qc,
        ranking,
        edit_manifest,
        rough_cut,
        final_qc,
        delivery,
        final_review,
        metadata,
    ]
    for field in CONTEXT_FIELDS:
        stale_document = deepcopy(dict(publish_approval.document))
        context = stale_document.get("gate_context")
        assert isinstance(context, dict)
        context[field] = "e" * 64
        if field == "effective_config_sha256":
            stale_document["effective_config_sha256"] = "e" * 64
        stale_publish = _snapshot(str(publish_approval.path), stale_document)
        stale_plan = plan_next_step(
            observe_episode_state([*lineage_without_publish, stale_publish]),
            "standard",
        )
        assert stale_plan.action_type == "approve_publish"
        assert any("context" in blocker for blocker in stale_plan.blockers)


def test_stale_storyboard_approval_does_not_grant_current_storyboard() -> None:
    snapshots = _generation_gate_snapshots()
    approval = next(
        item for item in snapshots if item.family == "storyboard-approval"
    )
    stale_document = deepcopy(dict(approval.document))
    stale_document["bound_artifacts"][0]["sha256"] = HASH_A
    stale = _snapshot(str(approval.path), stale_document)
    snapshots[snapshots.index(approval)] = stale
    observation = observe_episode_state(snapshots)
    plan = plan_next_step(observation, "standard")
    assert plan.action_type == "approve_storyboard"
    assert any("exactly match" in blocker for blocker in plan.blockers)


def test_mode_none_raises() -> None:
    observation = observe_episode_state([_brief()])
    with pytest.raises(OrchestrationPlanError, match="does not choose"):
        plan_next_step(observation, None)


def test_next_step_is_deterministic_and_schema_valid() -> None:
    observation = observe_episode_state([_brief()])
    one = plan_next_step(observation, "rapid")
    two = plan_next_step(observation, "rapid")
    assert one.next_step_id == two.next_step_id
    document = next_step_to_mapping(one)
    assert document["artifact_version"] == "next-step/1.0"
    assert document["rules_version"] == RULES
    assert document["auto_execution"] is False
    result = validate_artifact(document)
    assert result.ok is True, result.error_texts


def test_rules_version_must_not_be_fabricated_for_empty_observation() -> None:
    observation = observe_episode_state([])
    plan = plan_next_step(observation, "rapid")
    with pytest.raises(OrchestrationPlanError, match="rules_version"):
        next_step_to_mapping(plan)
    mapping = next_step_to_mapping(plan, rules_version="caller-rules")
    assert mapping["rules_version"] == "caller-rules"
