"""Hash-bound orchestration observation and next-step planning."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from video_factory.artifacts import validate_artifact
from video_factory.approvals import GateContext, gate_context_to_mapping
from video_factory.config import canonical_sha256
from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    HashDigest,
    OpaqueId,
    RelativeArtifactPath,
)
from video_factory.engine import (
    ArtifactSnapshot,
    OrchestrationPlanError,
    artifact_reference_to_mapping,
    build_declarative_gate_results,
    build_generation_readiness as _build_generation_readiness,
    make_artifact_snapshot,
    next_step_to_mapping,
    observe_episode_state,
    plan_next_step as _plan_next_step,
)
from video_factory.mutation import (
    WorkspaceObservation,
    PathNodeKind,
    PathObservation,
    WorkspaceRevision,
    WorkspaceRevisionOrigin,
    WorkspaceTrustState,
    workspace_revision_to_mapping,
)
from video_factory.authority import target_policy_bundle
from video_factory.workflow import (
    GateStatus,
    MaterialContextSeed,
    WorkflowContractError,
    build_gate_result,
    compare_legacy_parity,
    default_workflow_definition,
    evaluate_workflow,
    legacy_projection_from_plan,
    workflow_semantic_projection,
)


RULES = "rules-test"
EPISODE = "ep-synth"
HASH_A = "a" * 64
HASH_B = "b" * 64
GATE_CONTEXT = GateContext(
    workflow_definition_sha256=default_workflow_definition().definition_sha256,
    policy_bundle_sha256=target_policy_bundle().bundle_sha256,
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


def _workspace_observation() -> WorkspaceObservation:
    return WorkspaceObservation(
        workspace_id=OpaqueId("workspace-a"),
        revision_id=OpaqueId("revision-a"),
        manifest_sha256=HashDigest("4" * 64),
        trust_state=WorkspaceTrustState.TRUSTED,
        complete=True,
        entries=(),
    )


def _workspace_revision() -> WorkspaceRevision:
    return WorkspaceRevision(
        revision_id=OpaqueId("revision-a"),
        workspace_id=OpaqueId("workspace-a"),
        origin=WorkspaceRevisionOrigin.RECONCILED_BASELINE,
        parent_revision_id=None,
        reconciliation_evidence=ArtifactReference(
            RelativeArtifactPath("reconciliation/baseline.json"),
            HashDigest("9" * 64),
            ArtifactVersion("workspace-reconciliation/1.0"),
        ),
        manifest_sha256=HashDigest("4" * 64),
        created_at="2026-07-21T00:00:00Z",
        plan_id=OpaqueId("baseline-plan"),
        receipt_id=OpaqueId("baseline-receipt"),
        trust_state=WorkspaceTrustState.TRUSTED,
        entries=(),
    )


def _workspace_revision_sha256() -> str:
    return str(canonical_sha256(workspace_revision_to_mapping(_workspace_revision())))


def plan_next_step(observation, workflow_mode):
    """W00-compatible, non-authorizing planning overload."""

    return _plan_next_step(observation, workflow_mode)


def build_generation_readiness(observation, workflow_mode):
    """W00-compatible structural readiness overload."""

    return _build_generation_readiness(observation, workflow_mode)


def _strict_plan_next_step(observation, workflow_mode):
    return _plan_next_step(
        observation,
        workflow_mode,
        current_context=GATE_CONTEXT,
        evaluated_at=EVALUATED_AT,
        workspace_observation=_workspace_observation(),
        expected_workspace_id="workspace-a",
        expected_workspace_revision_id="revision-a",
        expected_workspace_revision=_workspace_revision(),
        expected_workspace_revision_sha256=_workspace_revision_sha256(),
    )


def _strict_build_generation_readiness(observation, workflow_mode):
    return _build_generation_readiness(
        observation,
        workflow_mode,
        current_context=GATE_CONTEXT,
        evaluated_at=EVALUATED_AT,
        workspace_observation=_workspace_observation(),
        expected_workspace_id="workspace-a",
        expected_workspace_revision_id="revision-a",
        expected_workspace_revision=_workspace_revision(),
        expected_workspace_revision_sha256=_workspace_revision_sha256(),
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


def _review_count(mode: str) -> int:
    return {"rapid": 0, "standard": 1, "controlled": 2}[mode]


def _generation_characterization_catalog(
    mode: str,
) -> dict[str, ArtifactSnapshot | tuple[ArtifactSnapshot, ...] | None]:
    brief = _brief()
    storyboard = _storyboard()
    storyboard_reviews = tuple(
        _review(
            f"02_storyboard/storyboard_review_{index + 1}.json",
            "storyboard-review",
            storyboard,
            reviewer=f"role:storyboard-reviewer-{index + 1}",
        )
        for index in range(_review_count(mode))
    )
    storyboard_approval = (
        None
        if mode == "rapid"
        else _approval(
            "02_storyboard/storyboard_approval.json",
            "storyboard-approval",
            "storyboard_approval",
            (storyboard, *storyboard_reviews),
        )
    )
    packet = _packet(storyboard)
    packet_reviews = tuple(
        _review(
            f"04_prompts/packet_review_{index + 1}.json",
            "packet-review",
            packet,
            reviewer=f"role:packet-reviewer-{index + 1}",
        )
        for index in range(_review_count(mode))
    )
    feasibility = _feasibility(packet, storyboard)
    packet_approval = (
        None
        if mode == "rapid"
        else _approval(
            "04_prompts/packet_approval.json",
            "packet-approval",
            "generation_approval",
            (packet, *packet_reviews, feasibility),
        )
    )
    return {
        "brief": brief,
        "storyboard": storyboard,
        "storyboard_reviews": storyboard_reviews,
        "storyboard_approval": storyboard_approval,
        "packet": packet,
        "packet_reviews": packet_reviews,
        "feasibility": feasibility,
        "packet_approval": packet_approval,
    }


def _present(*values: object) -> list[ArtifactSnapshot]:
    output: list[ArtifactSnapshot] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, tuple):
            output.extend(value)
        else:
            assert isinstance(value, ArtifactSnapshot)
            output.append(value)
    return output


def _single_shot_downstream_catalog(
    mode: str,
) -> dict[str, ArtifactSnapshot | tuple[ArtifactSnapshot, ...] | None]:
    catalog = _generation_characterization_catalog(mode)
    packet = catalog["packet"]
    brief = catalog["brief"]
    assert isinstance(packet, ArtifactSnapshot)
    assert isinstance(brief, ArtifactSnapshot)
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
    final_reviews = tuple(
        _review(
            f"08_qc/final_review_{index + 1}.json",
            "final-review",
            delivery,
            reviewer=f"role:final-reviewer-{index + 1}",
        )
        for index in range(_review_count(mode))
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
    publish_approval = (
        None
        if mode == "rapid"
        else _snapshot(
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
                    for item in (delivery, *final_reviews, metadata)
                ],
                "effective_config_sha256": HASH_B,
                "gate_context": gate_context_to_mapping(GATE_CONTEXT),
                "expires_at": "2026-07-21T05:00:00Z",
            },
        )
    )
    catalog.update(
        {
            "shot_qc": shot_qc,
            "ranking": ranking,
            "edit_manifest": edit_manifest,
            "rough_cut": rough_cut,
            "final_qc": final_qc,
            "delivery": delivery,
            "final_reviews": final_reviews,
            "metadata": metadata,
            "publish_approval": publish_approval,
        }
    )
    return catalog


def _two_shot_generation_characterization_snapshots(
    mode: str,
) -> list[ArtifactSnapshot]:
    snapshots = _two_shot_generation_gate_snapshots()
    if mode == "rapid":
        return [
            item
            for item in snapshots
            if item.family not in {
                "storyboard-review",
                "storyboard-approval",
                "packet-review",
                "packet-approval",
            }
        ]
    if mode == "standard":
        return snapshots

    storyboard = next(item for item in snapshots if item.family == "storyboard")
    packet = next(item for item in snapshots if item.family == "generation-packet")
    feasibility = next(
        item for item in snapshots if item.family == "generation-feasibility-review"
    )
    storyboard_reviews = [
        item for item in snapshots if item.family == "storyboard-review"
    ]
    storyboard_reviews.append(
        _review(
            "02_storyboard/storyboard_review_2.json",
            "storyboard-review",
            storyboard,
            reviewer="role:storyboard-reviewer-2",
        )
    )
    packet_reviews = [item for item in snapshots if item.family == "packet-review"]
    packet_reviews.append(
        _review(
            "04_prompts/packet_review_2.json",
            "packet-review",
            packet,
            reviewer="role:packet-reviewer-2",
        )
    )
    retained = [
        item
        for item in snapshots
        if item.family not in {
            "storyboard-review",
            "storyboard-approval",
            "packet-review",
            "packet-approval",
        }
    ]
    return [
        *retained,
        *storyboard_reviews,
        _approval(
            "02_storyboard/storyboard_approval.json",
            "storyboard-approval",
            "storyboard_approval",
            (storyboard, *storyboard_reviews),
        ),
        *packet_reviews,
        _approval(
            "04_prompts/packet_approval.json",
            "packet-approval",
            "generation_approval",
            (packet, *packet_reviews, feasibility),
        ),
    ]


CHARACTERIZATION_SEEDS = tuple(
    str(item.action_id) for item in default_workflow_definition().actions
)
EXPECTED_CHARACTERIZATION_ACTIONS = {
    "resolve_artifact_graph": ("resolve_artifact_graph",) * 3,
    "create_brief": ("create_brief",) * 3,
    "create_storyboard": ("create_storyboard",) * 3,
    "review_or_revise_storyboard": (
        "create_generation_packet",
        "review_or_revise_storyboard",
        "review_or_revise_storyboard",
    ),
    "approve_storyboard": (
        "create_generation_packet",
        "approve_storyboard",
        "approve_storyboard",
    ),
    "create_generation_packet": ("create_generation_packet",) * 3,
    "rebuild_generation_packet": ("rebuild_generation_packet",) * 3,
    "review_or_revise_generation_packet": (
        "review_generation_feasibility",
        "review_or_revise_generation_packet",
        "review_or_revise_generation_packet",
    ),
    "review_generation_feasibility": ("review_generation_feasibility",) * 3,
    "approve_generation": (
        "preview_complete",
        "approve_generation",
        "approve_generation",
    ),
    "preview_complete": (
        "preview_complete",
        "run_external_generation",
        "run_external_generation",
    ),
    "reconcile_workspace": (
        "preview_complete",
        "reconcile_workspace",
        "reconcile_workspace",
    ),
    "run_external_generation": (
        "preview_complete",
        "run_external_generation",
        "run_external_generation",
    ),
    "remediate_or_repeat_shot_qc": (
        "preview_complete",
        "remediate_or_repeat_shot_qc",
        "remediate_or_repeat_shot_qc",
    ),
    "run_continuity_qc": (
        "preview_complete", "run_continuity_qc", "run_continuity_qc"
    ),
    "remediate_continuity_qc": (
        "preview_complete", "remediate_continuity_qc", "remediate_continuity_qc"
    ),
    "rank_generation_candidates": (
        "preview_complete", "rank_generation_candidates", "rank_generation_candidates"
    ),
    "select_edit_inputs": (
        "preview_complete", "select_edit_inputs", "select_edit_inputs"
    ),
    "assemble_or_repair_rough_cut": (
        "preview_complete", "assemble_or_repair_rough_cut", "assemble_or_repair_rough_cut"
    ),
    "prepare_final_delivery": (
        "preview_complete", "prepare_final_delivery", "prepare_final_delivery"
    ),
    "repair_final_delivery": (
        "preview_complete", "repair_final_delivery", "repair_final_delivery"
    ),
    "review_or_revise_final_delivery": (
        "preview_complete",
        "review_or_revise_final_delivery",
        "review_or_revise_final_delivery",
    ),
    "prepare_publish_metadata": (
        "preview_complete", "prepare_publish_metadata", "prepare_publish_metadata"
    ),
    "repair_publish_metadata": (
        "preview_complete", "repair_publish_metadata", "repair_publish_metadata"
    ),
    "approve_publish": (
        "preview_complete", "approve_publish", "approve_publish"
    ),
    "ready_for_human_publish": (
        "preview_complete", "ready_for_human_publish", "ready_for_human_publish"
    ),
}


def _legacy_characterization_snapshots(
    seed: str,
    mode: str,
) -> list[ArtifactSnapshot]:
    catalog = _single_shot_downstream_catalog(mode)
    brief = catalog["brief"]
    storyboard = catalog["storyboard"]
    packet = catalog["packet"]
    assert isinstance(brief, ArtifactSnapshot)
    assert isinstance(storyboard, ArtifactSnapshot)
    assert isinstance(packet, ArtifactSnapshot)
    story_ready = _present(
        brief,
        storyboard,
        catalog["storyboard_reviews"],
        catalog["storyboard_approval"],
    )
    packet_ready = _present(
        *story_ready,
        packet,
        catalog["packet_reviews"],
        catalog["feasibility"],
        catalog["packet_approval"],
    )
    downstream = packet_ready

    if seed == "resolve_artifact_graph":
        return [
            _snapshot(
                "01_brief/invalid.json",
                {"artifact_version": "brief/1.0", "episode_id": EPISODE},
            )
        ]
    if seed == "create_brief":
        return []
    if seed == "create_storyboard":
        return [brief]
    if seed == "review_or_revise_storyboard":
        return [brief, storyboard]
    if seed == "approve_storyboard":
        return _present(brief, storyboard, catalog["storyboard_reviews"])
    if seed == "create_generation_packet":
        return story_ready
    if seed == "rebuild_generation_packet":
        document = deepcopy(dict(packet.document))
        document["storyboard_ref"]["sha256"] = "f" * 64
        return [*story_ready, _snapshot(str(packet.path), document)]
    if seed == "review_or_revise_generation_packet":
        return [*story_ready, packet]
    if seed == "review_generation_feasibility":
        return _present(
            *story_ready,
            packet,
            catalog["packet_reviews"],
        )
    if seed == "approve_generation":
        return _present(
            *story_ready,
            packet,
            catalog["packet_reviews"],
            catalog["feasibility"],
        )
    if seed in {"preview_complete", "reconcile_workspace", "run_external_generation"}:
        return downstream
    if seed == "remediate_or_repeat_shot_qc":
        document = deepcopy(dict(catalog["shot_qc"].document))  # type: ignore[union-attr]
        document["verdict"] = "fail"
        return [*downstream, _snapshot("08_qc/shot-01.json", document)]
    if seed in {"run_continuity_qc", "remediate_continuity_qc"}:
        two_shot = _two_shot_generation_characterization_snapshots(mode)
        qcs = _two_shot_qcs()
        if seed == "run_continuity_qc":
            return [*two_shot, *qcs]
        return [*two_shot, *qcs, _continuity_qc(qcs, verdict="fail")]
    if seed == "rank_generation_candidates":
        return _present(*downstream, catalog["shot_qc"])
    if seed == "select_edit_inputs":
        return _present(*downstream, catalog["shot_qc"], catalog["ranking"])
    if seed == "assemble_or_repair_rough_cut":
        return _present(
            *downstream,
            catalog["shot_qc"],
            catalog["ranking"],
            catalog["edit_manifest"],
        )
    if seed == "prepare_final_delivery":
        return _present(
            *downstream,
            catalog["shot_qc"],
            catalog["ranking"],
            catalog["edit_manifest"],
            catalog["rough_cut"],
        )
    delivery_prefix = _present(
        *downstream,
        catalog["shot_qc"],
        catalog["ranking"],
        catalog["edit_manifest"],
        catalog["rough_cut"],
        catalog["final_qc"],
    )
    if seed == "repair_final_delivery":
        delivery = catalog["delivery"]
        assert isinstance(delivery, ArtifactSnapshot)
        document = deepcopy(dict(delivery.document))
        document["lineage_refs"][1]["sha256"] = "f" * 64
        return [*delivery_prefix, _snapshot(str(delivery.path), document)]
    delivery_prefix = _present(*delivery_prefix, catalog["delivery"])
    if seed == "review_or_revise_final_delivery":
        return delivery_prefix
    reviewed_prefix = _present(*delivery_prefix, catalog["final_reviews"])
    if seed == "prepare_publish_metadata":
        return reviewed_prefix
    if seed == "repair_publish_metadata":
        metadata = catalog["metadata"]
        assert isinstance(metadata, ArtifactSnapshot)
        document = deepcopy(dict(metadata.document))
        document["source_brief_ref"]["sha256"] = "f" * 64
        return [*reviewed_prefix, _snapshot(str(metadata.path), document)]
    metadata_prefix = _present(*reviewed_prefix, catalog["metadata"])
    if seed == "approve_publish":
        return metadata_prefix
    if seed == "ready_for_human_publish":
        return _present(*metadata_prefix, catalog["publish_approval"])
    raise AssertionError(f"unknown characterization seed: {seed}")


def _characterization_inputs(seed: str, mode: str):
    observation = observe_episode_state(_legacy_characterization_snapshots(seed, mode))
    kwargs = {
        "current_context": GATE_CONTEXT,
        "evaluated_at": EVALUATED_AT,
        "workspace_observation": (
            None if seed == "reconcile_workspace" else _workspace_observation()
        ),
        "expected_workspace_id": "workspace-a",
        "expected_workspace_revision_id": "revision-a",
        "expected_workspace_revision": _workspace_revision(),
        "expected_workspace_revision_sha256": _workspace_revision_sha256(),
    }
    return observation, kwargs


def _legacy_characterization_plan(seed: str, mode: str):
    observation, kwargs = _characterization_inputs(seed, mode)
    return _plan_next_step(
        observation,
        mode,
        **kwargs,
    )


def _declarative_characterization(seed: str, mode: str):
    definition = default_workflow_definition()
    context = MaterialContextSeed(
        workflow_definition_sha256=GATE_CONTEXT.workflow_definition_sha256,
        policy_bundle_sha256=GATE_CONTEXT.policy_bundle_sha256,
        rules_bundle_sha256=GATE_CONTEXT.rules_bundle_sha256,
        effective_config_sha256=GATE_CONTEXT.effective_config_sha256,
        current_manifest_sha256=GATE_CONTEXT.current_manifest_sha256,
        evidence_graph_sha256=GATE_CONTEXT.evidence_graph_sha256,
    )
    observation, kwargs = _characterization_inputs(seed, mode)
    results = build_declarative_gate_results(
        observation,
        mode,
        context,
        **kwargs,
    )
    return evaluate_workflow(
        definition,
        results,
        context,
    )


@pytest.mark.parametrize("mode", ("rapid", "standard", "controlled"))
@pytest.mark.parametrize("seed", CHARACTERIZATION_SEEDS)
def test_legacy_and_declarative_evaluators_dual_run_over_78_rows(
    seed: str,
    mode: str,
) -> None:
    legacy_plan = _legacy_characterization_plan(seed, mode)
    mode_index = ("rapid", "standard", "controlled").index(mode)
    expected_action = EXPECTED_CHARACTERIZATION_ACTIONS[seed][mode_index]
    assert legacy_plan.action_type == expected_action
    declarative = _declarative_characterization(seed, mode)
    assert str(declarative.recommended_action_id) == expected_action
    report = compare_legacy_parity(
        legacy_projection_from_plan(legacy_plan),
        declarative,
        explained_dimensions={
            "blockers": "EXPLAINED_FRONTIER_EXPANSION",
            "consumed_evidence": "EXPLAINED_BLUEPRINT_CONSOLIDATION",
            "required_authority": "EXPLAINED_AUTHORITY_HARDENING",
        },
    )
    assert report.parity_pass, (seed, mode, report.unexplained_dimensions)
    assert report.cutover_applied is False
    assert report.authority_effect == "none"


def test_characterization_matrix_covers_every_target_action_identity() -> None:
    assert tuple(EXPECTED_CHARACTERIZATION_ACTIONS) == CHARACTERIZATION_SEEDS
    observed = {
        action
        for actions in EXPECTED_CHARACTERIZATION_ACTIONS.values()
        for action in actions
    }
    assert observed == set(CHARACTERIZATION_SEEDS)


def test_actual_adapter_late_evidence_change_reuses_unaffected_claims() -> None:
    definition = default_workflow_definition()
    context = MaterialContextSeed(
        workflow_definition_sha256=GATE_CONTEXT.workflow_definition_sha256,
        policy_bundle_sha256=GATE_CONTEXT.policy_bundle_sha256,
        rules_bundle_sha256=GATE_CONTEXT.rules_bundle_sha256,
        effective_config_sha256=GATE_CONTEXT.effective_config_sha256,
        current_manifest_sha256=GATE_CONTEXT.current_manifest_sha256,
        evidence_graph_sha256=GATE_CONTEXT.evidence_graph_sha256,
    )
    snapshots = _legacy_characterization_snapshots(
        "ready_for_human_publish", "standard"
    )
    observation = observe_episode_state(snapshots)
    kwargs = _characterization_inputs(
        "ready_for_human_publish", "standard"
    )[1]
    baseline_results = build_declarative_gate_results(
        observation, "standard", context, **kwargs
    )
    baseline = evaluate_workflow(definition, baseline_results, context)

    without_publish_approval = observe_episode_state(
        tuple(
            snapshot
            for snapshot in snapshots
            if snapshot.family != "publish-approval"
        )
    )
    changed_results = build_declarative_gate_results(
        without_publish_approval, "standard", context, **kwargs
    )
    incremental = evaluate_workflow(
        definition, changed_results, context, previous=baseline
    )
    clean = evaluate_workflow(definition, changed_results, context)

    assert workflow_semantic_projection(
        incremental
    ) == workflow_semantic_projection(clean)
    assert "artifact_graph_valid" in incremental.reused_claim_ids
    assert "brief_present" in incremental.reused_claim_ids
    assert "publish_approval_current" in incremental.invalidated_claim_ids
    assert "external_publish_complete" in incremental.invalidated_claim_ids
    assert len(incremental.invalidated_claim_ids) < len(definition.claims)


@pytest.mark.parametrize(
    "field",
    (
        "workflow_definition_sha256",
        "policy_bundle_sha256",
        "rules_bundle_sha256",
        "effective_config_sha256",
        "current_manifest_sha256",
        "evidence_graph_sha256",
    ),
)
def test_declarative_adapter_rejects_split_material_context(field: str) -> None:
    definition = default_workflow_definition()
    context = MaterialContextSeed(
        workflow_definition_sha256=GATE_CONTEXT.workflow_definition_sha256,
        policy_bundle_sha256=GATE_CONTEXT.policy_bundle_sha256,
        rules_bundle_sha256=GATE_CONTEXT.rules_bundle_sha256,
        effective_config_sha256=GATE_CONTEXT.effective_config_sha256,
        current_manifest_sha256=GATE_CONTEXT.current_manifest_sha256,
        evidence_graph_sha256=GATE_CONTEXT.evidence_graph_sha256,
    )
    observation, kwargs = _characterization_inputs(
        "ready_for_human_publish", "standard"
    )
    mismatched = replace(GATE_CONTEXT, **{field: HashDigest("f" * 64)})
    kwargs = {**kwargs, "current_context": mismatched}
    with pytest.raises(WorkflowContractError, match="differs from the evaluation"):
        build_declarative_gate_results(
            observation, "standard", context, **kwargs
        )


@pytest.mark.parametrize(
    ("current_context", "evaluated_at"),
    ((None, EVALUATED_AT), (GATE_CONTEXT, None)),
)
def test_declarative_adapter_missing_approval_context_never_reaches_effect(
    current_context,
    evaluated_at,
) -> None:
    definition = default_workflow_definition()
    context = MaterialContextSeed(
        workflow_definition_sha256=GATE_CONTEXT.workflow_definition_sha256,
        policy_bundle_sha256=GATE_CONTEXT.policy_bundle_sha256,
        rules_bundle_sha256=GATE_CONTEXT.rules_bundle_sha256,
        effective_config_sha256=GATE_CONTEXT.effective_config_sha256,
        current_manifest_sha256=GATE_CONTEXT.current_manifest_sha256,
        evidence_graph_sha256=GATE_CONTEXT.evidence_graph_sha256,
    )
    observation, kwargs = _characterization_inputs(
        "ready_for_human_publish", "standard"
    )
    kwargs = {
        **kwargs,
        "current_context": current_context,
        "evaluated_at": evaluated_at,
    }
    results = build_declarative_gate_results(
        observation, "standard", context, **kwargs
    )
    approval_results = tuple(
        value for value in results if "approval" in str(value.gate_id)
    )
    assert approval_results
    assert all(value.status is GateStatus.BLOCKED for value in approval_results)
    evaluation = evaluate_workflow(definition, results, context)
    assert str(evaluation.recommended_action_id) not in {
        "run_external_generation",
        "ready_for_human_publish",
    }


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
    assert readiness.authorization_ready is False
    assert readiness.packet is not None
    assert readiness.feasibility_review is not None
    assert readiness.approval_evidence is not None
    plan = plan_next_step(observation, "standard")
    assert plan.action_type == "run_external_generation"
    assert "paid_external_generation" in plan.prohibited_actions

    authorized = _strict_build_generation_readiness(observation, "standard")
    assert authorized.ready is True
    assert authorized.authorization_ready is False
    assert authorized.authority_effect == "none"
    assert authorized.requires_authority_decision is True
    assert authorized.gate_context_sha256 is not None
    assert authorized.workspace_revision_id == "revision-a"
    assert authorized.workspace_observation_sha256 is not None
    assert authorized.valid_from == "2026-07-21T01:00:00Z"
    assert authorized.valid_until == "2026-07-21T06:00:00Z"


def test_generation_and_publish_planning_fail_closed_without_trusted_workspace() -> None:
    observation = observe_episode_state(_generation_gate_snapshots())
    missing = _build_generation_readiness(
        observation,
        "standard",
        current_context=GATE_CONTEXT,
        evaluated_at=EVALUATED_AT,
        expected_workspace_revision_id="revision-a",
    )
    assert missing.ready is False
    assert missing.authorization_ready is False
    assert "mutation.workspace.observation_missing" in missing.blockers

    blocked_plan = _plan_next_step(
        observation,
        "standard",
        current_context=GATE_CONTEXT,
        evaluated_at=EVALUATED_AT,
        expected_workspace_revision_id="revision-a",
    )
    assert blocked_plan.action_type == "reconcile_workspace"
    assert "mutation.workspace.observation_missing" in blocked_plan.blockers

    untrusted = replace(
        _workspace_observation(), trust_state=WorkspaceTrustState.UNTRUSTED
    )
    untrusted_plan = _plan_next_step(
        observation,
        "standard",
        current_context=GATE_CONTEXT,
        evaluated_at=EVALUATED_AT,
        workspace_observation=untrusted,
        expected_workspace_revision_id="revision-a",
    )
    assert untrusted_plan.action_type == "reconcile_workspace"
    assert "mutation.workspace.untrusted" in untrusted_plan.blockers

    drifted = replace(
        _workspace_observation(),
        entries=(
            PathObservation(
                RelativeArtifactPath("unplanned.txt"),
                PathNodeKind.FILE,
                HashDigest("a" * 64),
                1,
            ),
        ),
    )
    drifted_readiness = _build_generation_readiness(
        observation,
        "standard",
        current_context=GATE_CONTEXT,
        evaluated_at=EVALUATED_AT,
        workspace_observation=drifted,
        expected_workspace_id="workspace-a",
        expected_workspace_revision_id="revision-a",
        expected_workspace_revision=_workspace_revision(),
        expected_workspace_revision_sha256=_workspace_revision_sha256(),
    )
    assert drifted_readiness.authorization_ready is False
    assert "mutation.workspace.content_drift" in drifted_readiness.blockers

    foreign = replace(
        _workspace_observation(), workspace_id=OpaqueId("workspace-foreign")
    )
    foreign_plan = _plan_next_step(
        observation,
        "standard",
        current_context=GATE_CONTEXT,
        evaluated_at=EVALUATED_AT,
        workspace_observation=foreign,
        expected_workspace_id="workspace-a",
        expected_workspace_revision_id="revision-a",
        expected_workspace_revision=_workspace_revision(),
        expected_workspace_revision_sha256=_workspace_revision_sha256(),
    )
    assert foreign_plan.action_type == "reconcile_workspace"
    assert "mutation.workspace.workspace_mismatch" in foreign_plan.blockers


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
    expired_plan = _strict_plan_next_step(
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
    duplicate_plan = _strict_plan_next_step(
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

    drifted_publish = _plan_next_step(
        observation,
        "standard",
        current_context=GATE_CONTEXT,
        evaluated_at=EVALUATED_AT,
        workspace_observation=replace(
            _workspace_observation(),
            entries=(
                PathObservation(
                    RelativeArtifactPath("unplanned.txt"),
                    PathNodeKind.FILE,
                    HashDigest("a" * 64),
                    1,
                ),
            ),
        ),
        expected_workspace_id="workspace-a",
        expected_workspace_revision_id="revision-a",
        expected_workspace_revision=_workspace_revision(),
        expected_workspace_revision_sha256=_workspace_revision_sha256(),
    )
    assert drifted_publish.action_type == "reconcile_workspace"
    assert "mutation.workspace.content_drift" in drifted_publish.blockers

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
        stale_plan = _strict_plan_next_step(
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


def test_plan_identity_is_invariant_to_artifact_input_order() -> None:
    snapshots = _generation_gate_snapshots(include_packet_approval=False)
    forward = observe_episode_state(snapshots)
    reverse = observe_episode_state(list(reversed(snapshots)))
    one = _plan_next_step(forward, "standard")
    two = _plan_next_step(reverse, "standard")
    assert one.action_type == two.action_type
    assert one.next_step_id == two.next_step_id


def test_rules_version_must_not_be_fabricated_for_empty_observation() -> None:
    observation = observe_episode_state([])
    plan = plan_next_step(observation, "rapid")
    with pytest.raises(OrchestrationPlanError, match="rules_version"):
        next_step_to_mapping(plan)
    mapping = next_step_to_mapping(plan, rules_version="caller-rules")
    assert mapping["rules_version"] == "caller-rules"
