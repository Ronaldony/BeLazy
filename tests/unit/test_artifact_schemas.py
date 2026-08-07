"""Synthetic tests for production artifact schemas and the validation runner."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from video_factory.artifacts import (
    ArtifactSchemaRegistry,
    clear_default_registry,
    get_default_registry,
    validate_artifact,
    validate_artifact_directory,
)
from video_factory.cli import handle_validate, run_validate


ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "schemas"
SHA_A = "a" * 64
SHA_B = "b" * 64

P15_ARTIFACT_VERSIONS = (
    "brief/1.0",
    "idea-candidates/1.0",
    "idea-scorecard/1.0",
    "idea-scores/1.0",
    "storyboard/1.0",
    "storyboard-review/1.0",
    "storyboard-approval/1.0",
    "reference-manifest/1.0",
    "reference-review/1.0",
)

P16_ARTIFACT_VERSIONS = (
    "generation-packet/1.0",
    "generation-packet/2.0",
    "packet-review/1.0",
    "packet-approval/1.0",
    "packet-approval/2.0",
    "storyboard-approval/2.0",
    "approval-requirement/1.0",
    "generation-feasibility-review/1.0",
    "external-call-reservation/1.0",
    "candidate-ranking/1.0",
    "shot-qc/1.0",
    "shot-qc/2.0",
    "edit-manifest/1.0",
    "rough-cut-report/1.0",
    "final-delivery/1.0",
    "final-review/1.0",
    "publish-metadata-draft/1.0",
    "publish-approval/1.0",
    "generation-day-brief/1.0",
    "handoff-event/1.0",
    "handoff-task/1.0",
    "handoff-result/1.0",
    "next-step/1.0",
)

P18_ARTIFACT_VERSIONS = (
    "analytics-record/1.0",
    "retro-report/1.0",
)

#: 0.3 교차 샷 연속성 계약.
P30_ARTIFACT_VERSIONS = (
    "generation-packet/2.1",
    "continuity-qc/1.0",
)

ARTIFACT_VERSIONS = (
    P15_ARTIFACT_VERSIONS
    + P16_ARTIFACT_VERSIONS
    + P18_ARTIFACT_VERSIONS
    + P30_ARTIFACT_VERSIONS
)
PRODUCTION_ARTIFACT_COUNT = 36


def _valid_brief() -> dict[str, object]:
    return {
        "artifact_version": "brief/1.0",
        "rules_version": "rules-bundle-1",
        "episode_id": "ep-demo-001",
        "summary": "A worker tests an impossible device.",
        "hook": "Device claims to reverse time for toast.",
        "development": "Each attempt creates a new absurd constraint.",
        "ending": "Toast is perfect; calendar is not.",
        "risks": ["continuity of props"],
    }


def _valid_idea_candidates() -> dict[str, object]:
    return {
        "artifact_version": "idea-candidates/1.0",
        "rules_version": "rules-bundle-1",
        "episode_id": "ep-demo-001",
        "generated_by": "role:writer",
        "candidates": [
            {
                "candidate_id": "cand-01",
                "title_working": "Toast time machine",
                "premise": "A device that only reverse-toasts.",
            },
            {
                "candidate_id": "cand-02",
                "title_working": "Paperclip bureaucracy",
                "premise": "Office supplies file formal complaints.",
            },
        ],
    }


def _valid_idea_scores() -> dict[str, object]:
    return {
        "artifact_version": "idea-scores/1.0",
        "rules_version": "rules-bundle-1",
        "episode_id": "ep-demo-001",
        "candidate_source_sha256": SHA_A,
        "scored_by": "role:scorer-a",
        "scores": [
            {
                "candidate_id": "cand-01",
                "rubric": {"clarity": 2, "surprise": 1},
                "notes": "solid hook",
            },
            {
                "candidate_id": "cand-02",
                "rubric": {"clarity": 1, "surprise": 2},
            },
        ],
    }


def _valid_idea_scorecard() -> dict[str, object]:
    return {
        "artifact_version": "idea-scorecard/1.0",
        "rules_version": "rules-bundle-1",
        "episode_id": "ep-demo-001",
        "generated_at": "2026-07-21T00:00:00Z",
        "candidate_source": {
            "path": "01_brief/idea_candidates.json",
            "sha256": SHA_A,
            "generated_by": "role:writer",
        },
        "score_sources": [
            {
                "path": "01_brief/scores_a.json",
                "sha256": SHA_B,
                "scored_by": "role:scorer-a",
            }
        ],
        "rubric_keys": ["clarity", "surprise"],
        "candidates": [
            {
                "candidate_id": "cand-01",
                "title_working": "Toast time machine",
                "scores": [
                    {
                        "scored_by": "role:scorer-a",
                        "rubric": {"clarity": 2, "surprise": 1},
                        "subtotal": 3,
                    }
                ],
                "total_score": 3,
                "score_order": 1,
            }
        ],
        "selection_policy": {
            "automatic_selection": False,
            "selected_candidate_id": None,
            "final_actor": "role:human-operator",
            "notice": "Human selects; ranking is advisory only.",
        },
    }


def _valid_storyboard(*, characters: list[str] | None = None) -> dict[str, object]:
    return {
        "artifact_version": "storyboard/1.0",
        "rules_version": "rules-bundle-1",
        "episode_id": "ep-demo-001",
        "generated_by": "role:writer",
        "premise": "Impossible toast device.",
        "shots": [
            {
                "shot_id": "shot-01",
                "duration_sec": 4.5,
                "narrative_role": "hook",
                "characters": [] if characters is None else characters,
                "location": "loc-lab-01",
                "camera": {"framing": "wide", "movement": "static"},
                "action": "Worker places bread into the device.",
                "creative_direction": (
                    "Play the reveal as deadpan corporate demo; "
                    "no slapstick, hold one beat longer than comfort."
                ),
                "start_state": "bread raw",
                "end_state": "device armed",
            }
        ],
    }


def _valid_storyboard_review() -> dict[str, object]:
    return {
        "artifact_version": "storyboard-review/1.0",
        "rules_version": "rules-bundle-1",
        "episode_id": "ep-demo-001",
        "subject": {
            "path": "02_storyboard/storyboard.json",
            "sha256": SHA_A,
            "artifact_version": "storyboard/1.0",
        },
        "creator_role": "role:writer",
        "reviewer_role": "role:reviewer",
        "reviewed_at": "2026-07-21T01:00:00Z",
        "verdict": "pass",
        "findings": [],
    }


def _valid_storyboard_approval() -> dict[str, object]:
    return {
        "artifact_version": "storyboard-approval/1.0",
        "rules_version": "rules-bundle-1",
        "episode_id": "ep-demo-001",
        "approved_by_human": True,
        "approver_role": "role:human-operator",
        "approved_at": "2026-07-21T02:00:00Z",
        "bound_artifacts": [
            {
                "path": "02_storyboard/storyboard.json",
                "sha256": SHA_A,
                "artifact_version": "storyboard/1.0",
            },
            {
                "path": "02_storyboard/storyboard_review.json",
                "sha256": SHA_B,
                "artifact_version": "storyboard-review/1.0",
            },
        ],
    }


def _valid_reference_manifest() -> dict[str, object]:
    return {
        "artifact_version": "reference-manifest/1.0",
        "rules_version": "rules-bundle-1",
        "episode_id": "ep-demo-001",
        "selected_by_human": True,
        "selected_by": "role:human-operator",
        "selected_at": "2026-07-21T03:00:00Z",
        "assets": [
            {
                "path": "05_references/char_front.png",
                "sha256": SHA_A,
                "role": "character-front",
            }
        ],
    }


def _valid_reference_review() -> dict[str, object]:
    return {
        "artifact_version": "reference-review/1.0",
        "rules_version": "rules-bundle-1",
        "episode_id": "ep-demo-001",
        "asset_creator_role": "role:asset-creator",
        "reviewer_role": "role:reviewer",
        "reviewed_at": "2026-07-21T04:00:00Z",
        "assets": [
            {
                "path": "05_references/char_front.png",
                "sha256": SHA_A,
                "verdict": "pass",
                "selected_for_draft": True,
                "reasons": ["matches declared silhouette"],
            }
        ],
    }


def _valid_generation_packet() -> dict[str, object]:
    return {
        "artifact_version": "generation-packet/1.0",
        "rules_version": "rules-bundle-1",
        "episode_id": "ep-demo-001",
        "generated_by": "role:writer",
        "approved_by_human": False,
        "reference_manifest": {
            "path": "05_references/reference_manifest.json",
            "sha256": SHA_A,
        },
        "quota_plan": {
            "date": "2026-07-21",
            "budgets": {
                "media-adapter-a": {"unit": "generations", "amount": 4},
            },
        },
        "output": {
            "dir": "06_generated",
            "filename_pattern": "{episode}_{shot}_v{version}.mp4",
            "target": {"aspect": "vertical", "min_height": 720, "fps_min": 24},
        },
        "shots": [
            {
                "shot_id": "shot-01",
                "duration_sec": 5.0,
                "candidates": 2,
                "mode": "image-to-video",
                "capability_id": "cap.media.generate",
                "prompt": "Worker places bread; device hums deadpan.",
                "reference_assets": [
                    {"path": "05_references/char_front.png", "sha256": SHA_B}
                ],
                "provider_plans": [
                    {
                        "adapter_id": "adapter.media.a",
                        "capability_id": "cap.media.generate",
                        "candidates": 2,
                        "params": {"duration": 5},
                    }
                ],
            }
        ],
    }


def _valid_generation_packet_v2() -> dict[str, object]:
    return {
        "artifact_version": "generation-packet/2.0",
        "rules_version": "rules-bundle-1",
        "episode_id": "ep-demo-001",
        "generated_by": "role:writer",
        "approved_by_human": False,
        "storyboard_ref": {
            "path": "02_storyboard/storyboard.json",
            "sha256": SHA_B,
            "artifact_version": "storyboard/1.0",
        },
        "output": {
            "dir": "06_generated",
            "filename_pattern": "{episode}_{shot}_v{version}.mp4",
            "target": {
                "width": 720,
                "height": 1280,
                "fps_min": 24,
                "audio_required": False,
            },
        },
        "shots": [
            {
                "shot_id": "shot-01",
                "source_shot_id": "shot-01",
                "duration_sec": 5.0,
                "candidates": 2,
                "mode": "image-to-video",
                "capability_id": "cap.media.generate",
                "prompt": "Worker places bread; device hums deadpan.",
                "first_frame": {
                    "path": "05_references/shot-01-before.png",
                    "sha256": SHA_A,
                    "width": 720,
                    "height": 1280,
                    "state": "before",
                },
                "continuity": {
                    "required": True,
                    "group_id": "lab-set",
                    "master_plate_ref": {
                        "path": "05_references/lab-master.png",
                        "sha256": SHA_B,
                        "artifact_version": "reference-image/1.0",
                    },
                },
                "render_dependencies": [
                    {"feature_id": "screen-text", "handled_in": "edit"}
                ],
            }
        ],
    }


def _valid_generation_feasibility_review() -> dict[str, object]:
    kinds = (
        "capability_binding",
        "minimum_duration",
        "first_frame_aspect",
        "first_frame_before_state",
        "continuity_anchor",
        "unsupported_render_dependency",
    )
    return {
        "artifact_version": "generation-feasibility-review/1.0",
        "rules_version": "rules-bundle-1",
        "episode_id": "ep-demo-001",
        "subject": {
            "path": "04_prompts/generation_packet_v2.json",
            "sha256": SHA_A,
            "artifact_version": "generation-packet/2.0",
        },
        "storyboard_ref": {
            "path": "02_storyboard/storyboard.json",
            "sha256": SHA_B,
            "artifact_version": "storyboard/1.0",
        },
        "capability_profile": {
            "profile_id": "profile-media",
            "sha256": SHA_A,
            "capability_id": "cap.media.generate",
        },
        "creator_role": "role:writer",
        "reviewer_role": "role:feasibility-reviewer",
        "reviewed_at": "2026-07-21T05:30:00Z",
        "checks": [
            {
                "check_id": f"shot-01:{kind}",
                "kind": kind,
                "shot_id": "shot-01",
                "status": "pass",
                "message": "injected capability facts pass",
            }
            for kind in kinds
        ],
        "verdict": "pass",
    }


def _valid_approval_requirement() -> dict[str, object]:
    return {
        "artifact_version": "approval-requirement/1.0",
        "rules_version": "rules-bundle-1",
        "episode_id": "ep-demo-001",
        "requirement_id": "req-generation-1",
        "capability_id": "generation_approval",
        "bound_artifacts": [
            {
                "path": "04_prompts/generation_packet_v2.json",
                "sha256": SHA_A,
                "artifact_version": "generation-packet/2.0",
            }
        ],
        "effective_config_sha256": SHA_B,
        "kind": "packet-approval",
        "creates_evidence": False,
    }


def _valid_approval_v2(family: str, capability: str) -> dict[str, object]:
    return {
        "artifact_version": f"{family}/2.0",
        "rules_version": "rules-bundle-1",
        "episode_id": "ep-demo-001",
        "requirement_id": f"req-{family}-1",
        "capability_id": capability,
        "evidence_id": f"evidence-{family}-1",
        "state": "granted",
        "approved_by_human": True,
        "approver_role": "human:operator",
        "approved_at": "2026-07-21T06:00:00Z",
        "record_sha256": SHA_A,
        "bound_artifacts": [
            {
                "path": "bound/current.json",
                "sha256": SHA_B,
                "artifact_version": "storyboard/1.0",
            }
        ],
        "effective_config_sha256": SHA_A,
    }


def _valid_storyboard_approval_v2() -> dict[str, object]:
    return _valid_approval_v2("storyboard-approval", "storyboard_approval")


def _valid_packet_approval_v2() -> dict[str, object]:
    return _valid_approval_v2("packet-approval", "generation_approval")


def _valid_shot_qc_v2() -> dict[str, object]:
    document = deepcopy(_valid_shot_qc())
    document["artifact_version"] = "shot-qc/2.0"
    document["shot_id"] = "shot-01"
    for key in ("adapter_id", "expectations_source"):
        document.pop(key, None)
    return document


def _valid_final_delivery() -> dict[str, object]:
    return {
        "artifact_version": "final-delivery/1.0",
        "rules_version": "rules-bundle-1",
        "episode_id": "ep-demo-001",
        "created_by": "role:editor",
        "created_at": "2026-07-21T10:00:00Z",
        "selected_output": {
            "path": "07_edit/final_v2.mp4",
            "sha256": SHA_A,
            "artifact_version": "media-output/1.0",
        },
        "lineage_refs": [
            {
                "path": "07_edit/rough_cut_report.json",
                "sha256": SHA_B,
                "artifact_version": "rough-cut-report/1.0",
            }
        ],
        "technical_qc_ref": {
            "path": "08_qc/final_qc.json",
            "sha256": SHA_A,
            "artifact_version": "shot-qc/2.0",
        },
        "technical_verdict": "warn",
    }


def _valid_final_review() -> dict[str, object]:
    return {
        "artifact_version": "final-review/1.0",
        "rules_version": "rules-bundle-1",
        "episode_id": "ep-demo-001",
        "subject": {
            "path": "07_edit/final_delivery.json",
            "sha256": SHA_A,
            "artifact_version": "final-delivery/1.0",
        },
        "creator_role": "role:editor",
        "reviewer_role": "role:reviewer",
        "reviewed_at": "2026-07-21T10:30:00Z",
        "verdict": "pass",
        "findings": [],
    }


def _valid_publish_approval() -> dict[str, object]:
    document = _valid_approval_v2("publish-approval", "publish_approval")
    document["artifact_version"] = "publish-approval/1.0"
    return document


def _valid_packet_review() -> dict[str, object]:
    return {
        "artifact_version": "packet-review/1.0",
        "rules_version": "rules-bundle-1",
        "episode_id": "ep-demo-001",
        "subject": {
            "path": "04_prompts/generation_packet.json",
            "sha256": SHA_A,
            "artifact_version": "generation-packet/1.0",
        },
        "creator_role": "role:writer",
        "reviewer_role": "role:reviewer",
        "reviewed_at": "2026-07-21T05:00:00Z",
        "verdict": "pass",
        "findings": [],
    }


def _valid_packet_approval() -> dict[str, object]:
    return {
        "artifact_version": "packet-approval/1.0",
        "rules_version": "rules-bundle-1",
        "episode_id": "ep-demo-001",
        "approved_by_human": True,
        "approver_role": "human:operator",
        "approved_at": "2026-07-21T06:00:00Z",
        "bound_artifacts": [
            {
                "path": "04_prompts/generation_packet.json",
                "sha256": SHA_A,
                "artifact_version": "generation-packet/1.0",
            },
            {
                "path": "04_prompts/packet_review.json",
                "sha256": SHA_B,
                "artifact_version": "packet-review/1.0",
            },
        ],
    }


def _valid_external_call_reservation() -> dict[str, object]:
    return {
        "artifact_version": "external-call-reservation/1.0",
        "rules_version": "rules-bundle-1",
        "event_id": "evt-reserve-001",
        "reserved_at": "2026-07-21T07:00:00Z",
        "actor_role": "role:orchestrator",
        "adapter_id": "adapter.media.a",
        "capability_id": "cap.media.generate",
        "purpose": "reserve one generation attempt",
        "idempotency_key": SHA_A,
        "status": "RESERVED",
        "generation_call": True,
        "quota_snapshot": {
            "unit": "generations",
            "calls_before": 0,
            "calls_after": 1,
            "limit": 4,
        },
        "bound_artifacts": [
            {
                "path": "04_prompts/generation_packet.json",
                "sha256": SHA_B,
                "artifact_version": "generation-packet/1.0",
            }
        ],
    }


def _valid_candidate_ranking() -> dict[str, object]:
    return {
        "artifact_version": "candidate-ranking/1.0",
        "rules_version": "rules-bundle-1",
        "episode_id": "ep-demo-001",
        "generated_at": "2026-07-21T08:00:00Z",
        "packet_ref": {
            "path": "04_prompts/generation_packet.json",
            "sha256": SHA_A,
            "artifact_version": "generation-packet/1.0",
        },
        "ranking_policy": ["warning_count asc", "sharpness desc"],
        "shots": [
            {
                "shot_id": "shot-01",
                "candidates": [
                    {
                        "rank": 1,
                        "file": "06_generated/shot01_v1.mp4",
                        "adapter_id": "adapter.media.a",
                        "qc_ref": {
                            "path": "08_qc/shot01_v1.json",
                            "sha256": SHA_B,
                        },
                        "media_sha256": SHA_A,
                        "metrics": {
                            "warning_count": 0,
                            "sharpness_laplacian_mean": 120.5,
                        },
                        "rough_cut_candidate": True,
                    }
                ],
            }
        ],
    }


def _valid_shot_qc() -> dict[str, object]:
    return {
        "artifact_version": "shot-qc/1.0",
        "rules_version": "rules-bundle-1",
        "episode_id": "ep-demo-001",
        "checked_at": "2026-07-21T08:30:00Z",
        "subject": {
            "path": "06_generated/shot01_v1.mp4",
            "sha256": SHA_A,
            "artifact_version": "media-clip/1.0",
        },
        "adapter_id": "adapter.media.a",
        "expectations_source": {
            "config_pointer": "/settings/media",
            "effective_config_sha256": SHA_B,
        },
        "measurements": [
            {"measurement_id": "width", "value": 1080, "unit": "px"},
            {"measurement_id": "height", "value": 1920, "unit": "px"},
            {"measurement_id": "fps", "value": 24, "unit": "fps"},
        ],
        "constraints": [
            {
                "constraint_id": "c-width",
                "measurement_id": "width",
                "comparison": "equal",
                "operands": [1080],
                "severity": "error",
            }
        ],
        "findings": [
            {
                "constraint_id": "c-width",
                "passed": True,
                "observed": {
                    "measurement_id": "width",
                    "value": 1080,
                    "unit": "px",
                },
                "message": "width matches expectation from config",
            }
        ],
        "verdict": "pass",
    }


def _valid_edit_manifest() -> dict[str, object]:
    return {
        "artifact_version": "edit-manifest/1.0",
        "rules_version": "rules-bundle-1",
        "episode_id": "ep-demo-001",
        "selected_by_human": True,
        "selected_by": "human:operator",
        "selected_at": "2026-07-21T09:00:00Z",
        "input_clips": [
            {
                "shot_id": "shot-01",
                "path": "06_generated/shot01_v1.mp4",
                "sha256": SHA_A,
                "duration_sec": 5.0,
            }
        ],
        "target_platforms": ["shorts-common"],
        "audio_required": False,
        "output_path": "07_edit/rough_cut.mp4",
    }


def _valid_rough_cut_report() -> dict[str, object]:
    return {
        "artifact_version": "rough-cut-report/1.0",
        "rules_version": "rules-bundle-1",
        "episode_id": "ep-demo-001",
        "checked_at": "2026-07-21T09:30:00Z",
        "status": "PASS",
        "ranking_ref": {
            "path": "08_qc/candidate_ranking.json",
            "sha256": SHA_A,
            "artifact_version": "candidate-ranking/1.0",
        },
        "inputs": [
            {
                "shot_id": "shot-01",
                "path": "06_generated/shot01_v1.mp4",
                "sha256": SHA_B,
                "duration_sec": 5.0,
            }
        ],
        "output_path": "07_edit/rough_cut.mp4",
        "local_tools": {"encoder_available": True, "probe_available": True},
        "command": ["local-encoder", "-i", "shot01_v1.mp4"],
        "qc": None,
    }


def _valid_publish_metadata_draft() -> dict[str, object]:
    return {
        "artifact_version": "publish-metadata-draft/1.0",
        "rules_version": "rules-bundle-1",
        "episode_id": "ep-demo-001",
        "drafted_at": "2026-07-21T10:00:00Z",
        "source_brief_ref": {
            "path": "01_brief/brief.json",
            "sha256": SHA_A,
            "artifact_version": "brief/1.0",
        },
        "title": "Impossible toast device demo",
        "description": "A worker tests a device that reverse-toasts bread.",
        "tags": ["demo", "product-test"],
        "ai_disclosure_draft": "Synthetic media produced with generative tools.",
        "human_review_required": True,
        "target_platforms": ["shorts-common"],
    }


def _valid_generation_day_brief() -> dict[str, object]:
    return {
        "artifact_version": "generation-day-brief/1.0",
        "rules_version": "rules-bundle-1",
        "episode_id": "ep-demo-001",
        "generated_at": "2026-07-21T11:00:00Z",
        "scorecard_ref": {
            "path": "01_brief/scorecard.json",
            "sha256": SHA_A,
        },
        "approval_items": [
            {
                "kind": "packet",
                "state": "PENDING_HUMAN",
                "target_path": "04_prompts/packet_approval.json",
                "target_sha256": None,
                "blockers": ["human approval missing"],
                "suggested_command": "review packet approval",
            }
        ],
        "quota_input": {
            "state": "CURRENT",
            "path": "04_prompts/generation_packet.json",
            "sha256": SHA_B,
            "quota": {"unit": "generations", "amount": 4},
            "message": "budget present",
        },
        "ready_for_generation": False,
        "automatic_approval_performed": False,
        "generation_executed": False,
    }


def _valid_handoff_event() -> dict[str, object]:
    return {
        "artifact_version": "handoff-event/1.0",
        "event_id": "event-001",
        "event_type": "TASK_READY",
        "occurred_at": "2026-07-21T12:00:00Z",
        "actor_role": "role:orchestrator",
        "episode_id": "ep-demo-001",
        "before_state": "DRAFT",
        "after_state": "READY",
        "task_id": "task-001",
        "task_sha256": SHA_A,
        "result_id": None,
        "result_sha256": None,
        "reason_code": None,
        "payload": {"note": "task prepared"},
    }


def _valid_handoff_task() -> dict[str, object]:
    return {
        "artifact_version": "handoff-task/1.0",
        "rules_version": "rules-bundle-1",
        "task_id": "task-001",
        "episode_id": "ep-demo-001",
        "created_at": "2026-07-21T12:00:00Z",
        "phase": "generation-prep",
        "pipeline_state": "READY",
        "mode": "create",
        "required_actor_role": "role:writer",
        "forbidden_actor_roles": ["role:reviewer"],
        "input_artifacts": [
            {
                "path": "02_storyboard/storyboard.json",
                "sha256": SHA_A,
                "artifact_version": "storyboard/1.0",
                "producer_role": "role:writer",
            }
        ],
        "allowed_outputs": [
            {
                "path_prefix": "04_prompts",
                "artifact_versions": ["generation-packet/1.0"],
            }
        ],
        "capability_allowlist": ["read_inputs", "write_allowed_outputs"],
        "parent_task_id": None,
        "idempotency_key": SHA_B,
        "human_gate_required": True,
        "summary": "Prepare generation packet from approved storyboard.",
    }


def _valid_handoff_result() -> dict[str, object]:
    return {
        "artifact_version": "handoff-result/1.0",
        "result_id": "result-001",
        "task_id": "task-001",
        "task_sha256": SHA_A,
        "request_id": "req-001",
        "episode_id": "ep-demo-001",
        "completed_at": "2026-07-21T13:00:00Z",
        "executor_role": "role:writer",
        "adapter_id": "adapter.executor.local",
        "outcome": "SUCCEEDED",
        "tool": {"name": "local-editor", "model": None, "version": "1"},
        "input_artifacts": [
            {"path": "02_storyboard/storyboard.json", "sha256": SHA_A}
        ],
        "outputs": [
            {
                "path": "04_prompts/generation_packet.json",
                "sha256": SHA_B,
                "artifact_version": "generation-packet/1.0",
            }
        ],
        "exit_code": 0,
        "review": None,
        "external_reference": {"request_id": None, "session_id": None},
        "measured_cost": {
            "amount": None,
            "unit": None,
            "is_unknown": True,
        },
        "uncertainty": {"uncertain": False, "reason": None},
        "audit": {
            "submitted_by": "role:writer",
            "submitted_at": "2026-07-21T13:00:01Z",
            "notes": "ok",
        },
    }


def _valid_next_step() -> dict[str, object]:
    return {
        "artifact_version": "next-step/1.0",
        "rules_version": "rules-bundle-1",
        "next_step_id": "next-001",
        "source_refs": {
            "task_id": "task-001",
            "result_id": "result-001",
            "result_sha256": SHA_A,
        },
        "completed_phase": "packet-prep",
        "next_phase": "human-approval",
        "next_actor_role": "human:operator",
        "action_type": "HUMAN_ACTION",
        "prerequisites": ["packet review pass"],
        "required_inputs": [
            {
                "path": "04_prompts/generation_packet.json",
                "sha256": SHA_B,
            }
        ],
        "blockers": [],
        "instructions": "Review and approve the generation packet.",
        "command_example": None,
        "target_paths": ["04_prompts/packet_approval.json"],
        "approval_required": True,
        "prohibited_actions": ["auto-generate media"],
        "auto_execution": False,
    }


def _valid_analytics_record() -> dict[str, object]:
    return {
        "artifact_version": "analytics-record/1.0",
        "rules_version": "rules-bundle-1",
        "episode_id": "ep-demo-001",
        "collected_at": "2026-07-21T15:00:00Z",
        "collector_role": "role:human-operator",
        "publish_record_ref": {
            "path": "09_publish/metadata_draft.json",
            "sha256": SHA_A,
            "artifact_version": "publish-metadata-draft/1.0",
        },
        "checkpoint_window": {
            "window_id": "window-alpha",
            "definition": "caller-defined observation window A",
        },
        "metrics": {
            "retention_index": 0.41,
            "share_yield": None,
        },
    }


def _valid_retro_report() -> dict[str, object]:
    return {
        "artifact_version": "retro-report/1.0",
        "rules_version": "rules-bundle-1",
        "episode_id": "ep-demo-001",
        "policy_id": "policy-demo",
        "checkpoint_window_ids": ["window-alpha"],
        "evaluations": [
            {
                "rule_id": "rule-1",
                "hypothesis_id": "hyp-a",
                "metric_key": "retention_index",
                "comparator": "gte",
                "threshold": 0.3,
                "observed_value": 0.41,
                "verdict": "supported",
                "evidence": "supported: observed=0.41 comparator=gte threshold=0.3",
                "source_window_id": "window-alpha",
                "source_collected_at": "2026-07-21T15:00:00Z",
            },
            {
                "rule_id": "rule-2",
                "hypothesis_id": "hyp-b",
                "metric_key": "share_yield",
                "comparator": "gte",
                "threshold": 0.01,
                "observed_value": None,
                "verdict": "inconclusive",
                "evidence": "inconclusive: metric value is null",
            },
        ],
        "counts": {
            "supported": 1,
            "refuted": 0,
            "inconclusive": 1,
        },
    }



def _valid_generation_packet_v2_1() -> dict[str, object]:
    """2.0 샘플에 교차 샷 상태 인계 선언만 더한다."""

    document = _valid_generation_packet_v2()
    document["artifact_version"] = "generation-packet/2.1"
    for shot in document["shots"]:
        shot["first_frame"]["depicted_elements"] = ["element-hero"]
        shot["continuity"]["carried_elements"] = []
    return document


def _valid_continuity_qc() -> dict[str, object]:
    """계약과 어긋나지 않도록 실제 공개 API로 문서를 만든다."""

    from video_factory.continuity import (
        ContinuityAxis,
        ContinuityExpectation,
        ContinuitySubject,
        build_continuity_qc_plan,
        continuity_qc_to_mapping,
        judge_continuity,
    )
    from video_factory.domain import ArtifactReference, HashDigest, OpaqueId
    from video_factory.qc import Measurement

    subjects = [
        ContinuitySubject(
            shot_id=OpaqueId(shot),
            artifact=ArtifactReference(
                path=f"06_generated/{shot}.mp4",
                sha256=HashDigest(digest),
                artifact_version="shot-qc/2.0",
            ),
        )
        for shot, digest in (("shot-01", SHA_A), ("shot-02", SHA_B))
    ]
    plan = build_continuity_qc_plan(
        subjects,
        [
            ContinuityExpectation(
                element_id=OpaqueId("element-hero"),
                axis=ContinuityAxis.PRESENCE,
                from_shot_id=OpaqueId("shot-01"),
                to_shot_id=OpaqueId("shot-02"),
            )
        ],
    )
    comparison = plan.comparisons[0]
    judgment = judge_continuity(
        plan,
        (
            Measurement(comparison.from_observation_id, True, None),
            Measurement(comparison.to_observation_id, True, None),
        ),
    )
    return continuity_qc_to_mapping(
        judgment,
        episode_id="ep-demo-001",
        rules_version="rules-bundle-1",
        checked_at="2026-07-28T09:00:00Z",
    )


VALID_BY_VERSION = {
    "brief/1.0": _valid_brief,
    "idea-candidates/1.0": _valid_idea_candidates,
    "idea-scorecard/1.0": _valid_idea_scorecard,
    "idea-scores/1.0": _valid_idea_scores,
    "storyboard/1.0": _valid_storyboard,
    "storyboard-review/1.0": _valid_storyboard_review,
    "storyboard-approval/1.0": _valid_storyboard_approval,
    "reference-manifest/1.0": _valid_reference_manifest,
    "reference-review/1.0": _valid_reference_review,
    "generation-packet/1.0": _valid_generation_packet,
    "generation-packet/2.0": _valid_generation_packet_v2,
    "generation-packet/2.1": _valid_generation_packet_v2_1,
    "continuity-qc/1.0": _valid_continuity_qc,
    "packet-review/1.0": _valid_packet_review,
    "packet-approval/1.0": _valid_packet_approval,
    "packet-approval/2.0": _valid_packet_approval_v2,
    "storyboard-approval/2.0": _valid_storyboard_approval_v2,
    "approval-requirement/1.0": _valid_approval_requirement,
    "generation-feasibility-review/1.0": _valid_generation_feasibility_review,
    "external-call-reservation/1.0": _valid_external_call_reservation,
    "candidate-ranking/1.0": _valid_candidate_ranking,
    "shot-qc/1.0": _valid_shot_qc,
    "shot-qc/2.0": _valid_shot_qc_v2,
    "edit-manifest/1.0": _valid_edit_manifest,
    "rough-cut-report/1.0": _valid_rough_cut_report,
    "final-delivery/1.0": _valid_final_delivery,
    "final-review/1.0": _valid_final_review,
    "publish-metadata-draft/1.0": _valid_publish_metadata_draft,
    "publish-approval/1.0": _valid_publish_approval,
    "generation-day-brief/1.0": _valid_generation_day_brief,
    "handoff-event/1.0": _valid_handoff_event,
    "handoff-task/1.0": _valid_handoff_task,
    "handoff-result/1.0": _valid_handoff_result,
    "next-step/1.0": _valid_next_step,
    "analytics-record/1.0": _valid_analytics_record,
    "retro-report/1.0": _valid_retro_report,
}


@pytest.fixture(autouse=True)
def _reset_registry_cache() -> None:
    clear_default_registry()
    yield
    clear_default_registry()


def test_registry_registers_all_production_artifact_families() -> None:
    registry = ArtifactSchemaRegistry(SCHEMAS)
    for version in ARTIFACT_VERSIONS:
        assert registry.has(version), version
    assert len(ARTIFACT_VERSIONS) == PRODUCTION_ARTIFACT_COUNT
    entry = registry.get("storyboard/1.0")
    assert entry.filename == "storyboard.schema.json"
    assert entry.family == "storyboard"
    gen = registry.get("generation-packet/1.0")
    assert gen.filename == "generation-packet.schema.json"
    assert gen.family == "generation-packet"


@pytest.mark.parametrize("version", ARTIFACT_VERSIONS)
def test_each_artifact_family_accepts_valid_sample(version: str) -> None:
    document = VALID_BY_VERSION[version]()
    result = validate_artifact(document)
    assert result.ok is True, result.error_texts
    assert result.artifact_version == version
    assert result.errors == ()


_REQUIRED_DROP = {
    "brief/1.0": "summary",
    "idea-candidates/1.0": "candidates",
    "idea-scorecard/1.0": "candidates",
    "idea-scores/1.0": "scores",
    "storyboard/1.0": "shots",
    "storyboard-review/1.0": "findings",
    "storyboard-approval/1.0": "bound_artifacts",
    "reference-manifest/1.0": "assets",
    "reference-review/1.0": "assets",
    "generation-packet/1.0": "shots",
    "generation-packet/2.0": "storyboard_ref",
    "generation-packet/2.1": "storyboard_ref",
    "continuity-qc/1.0": "comparisons",
    "packet-review/1.0": "findings",
    "packet-approval/1.0": "bound_artifacts",
    "packet-approval/2.0": "evidence_id",
    "storyboard-approval/2.0": "evidence_id",
    "approval-requirement/1.0": "requirement_id",
    "generation-feasibility-review/1.0": "checks",
    "external-call-reservation/1.0": "bound_artifacts",
    "candidate-ranking/1.0": "shots",
    "shot-qc/1.0": "findings",
    "shot-qc/2.0": "shot_id",
    "edit-manifest/1.0": "input_clips",
    "rough-cut-report/1.0": "inputs",
    "final-delivery/1.0": "selected_output",
    "final-review/1.0": "findings",
    "publish-metadata-draft/1.0": "title",
    "publish-approval/1.0": "evidence_id",
    "generation-day-brief/1.0": "approval_items",
    "handoff-event/1.0": "payload",
    "handoff-task/1.0": "input_artifacts",
    "handoff-result/1.0": "outcome",
    "next-step/1.0": "instructions",
    "analytics-record/1.0": "metrics",
    "retro-report/1.0": "evaluations",
}


@pytest.mark.parametrize("version", ARTIFACT_VERSIONS)
def test_each_artifact_family_rejects_missing_required_field(version: str) -> None:
    document = VALID_BY_VERSION[version]()
    removed = _REQUIRED_DROP[version]
    del document[removed]
    result = validate_artifact(document)
    assert result.ok is False
    assert any(removed in error.path or removed in error.message for error in result.errors)


def test_unregistered_artifact_version_fails_closed() -> None:
    document = _valid_brief()
    document["artifact_version"] = "not-a-real-family/9.9"
    result = validate_artifact(document)
    assert result.ok is False
    assert result.errors
    assert result.errors[0].validator == "registry"
    assert "unregistered artifact_version" in result.errors[0].message


def test_storyboard_allows_empty_character_array() -> None:
    document = _valid_storyboard(characters=[])
    result = validate_artifact(document)
    assert result.ok is True, result.error_texts
    assert document["shots"][0]["characters"] == []


def test_creative_direction_accepts_arbitrary_free_text() -> None:
    document = _valid_storyboard()
    free = (
        "Whatever timing the creator wants: pause, stare, shrug, "
        "then deliver the product line like a safety video. "
        "Emoji-like energy is fine as prose; no enum needed. ★"
    )
    document["shots"][0]["creative_direction"] = free
    result = validate_artifact(document)
    assert result.ok is True, result.error_texts


def test_brief_is_valid_without_marketing_reasons() -> None:
    document = _valid_brief()
    assert "marketing_reasons" not in document
    result = validate_artifact(document)
    assert result.ok is True, result.error_texts

    with_reasons = deepcopy(document)
    with_reasons["marketing_reasons"] = [
        {"reason_id": "stop", "statement": "Unexpected device claim."},
        {"reason_id": "stay", "statement": "Escalating constraint loop."},
    ]
    assert validate_artifact(with_reasons).ok is True


def test_idea_scorecard_forbids_automatic_selection_true() -> None:
    document = _valid_idea_scorecard()
    document["selection_policy"]["automatic_selection"] = True
    result = validate_artifact(document)
    assert result.ok is False


def test_batch_runner_aggregates_mixed_directory(tmp_path: Path) -> None:
    good = tmp_path / "good_brief.json"
    bad = tmp_path / "bad_brief.json"
    good.write_text(json.dumps(_valid_brief()), encoding="utf-8")
    bad.write_text(
        json.dumps(
            {
                "artifact_version": "brief/1.0",
                "rules_version": "x",
                "episode_id": "ep-1",
            }
        ),
        encoding="utf-8",
    )
    report = validate_artifact_directory(tmp_path)
    assert report.total == 2
    assert report.passed == 1
    assert report.failed == 1
    assert report.skipped == 0
    paths = {Path(item.path).name: item.result.ok for item in report.results}
    assert paths["good_brief.json"] is True
    assert paths["bad_brief.json"] is False


def test_cli_config_validate_remains_backward_compatible() -> None:
    fixtures = Path(__file__).resolve().parents[1] / "fixtures"
    valid = json.loads((fixtures / "channel_config.json").read_text(encoding="utf-8"))
    ok = run_validate(layer="channel", document=valid)
    assert ok.ok is True
    assert ok.kind == "config"
    assert ok.scope_id == "channel-a"

    bad = run_validate(
        layer="channel",
        document={"artifact_version": "channel-config/1.0", "config_contract": "1.0"},
    )
    assert bad.ok is False
    assert bad.kind == "config"


def test_cli_artifact_validate_path(tmp_path: Path) -> None:
    path = tmp_path / "brief.json"
    path.write_text(json.dumps(_valid_brief()), encoding="utf-8")
    report = run_validate(path=path)
    assert report.ok is True
    assert report.kind == "artifact"
    assert report.artifact_version == "brief/1.0"
    result = handle_validate(path=path)
    assert result.exit_code == 0


def test_default_registry_lists_production_versions() -> None:
    registry = get_default_registry()
    versions = set(registry.list_versions())
    assert set(ARTIFACT_VERSIONS).issubset(versions)


def test_reference_manifest_rejects_absolute_and_parent_paths() -> None:
    document = _valid_reference_manifest()
    document["assets"][0]["path"] = "/abs/asset.png"
    assert validate_artifact(document).ok is False

    parent = _valid_reference_manifest()
    parent["assets"][0]["path"] = "../escape/asset.png"
    assert validate_artifact(parent).ok is False


def test_candidate_ranking_schema_has_no_automatic_selection_fields() -> None:
    registry = ArtifactSchemaRegistry(SCHEMAS)
    schema = registry.schema_for_version("candidate-ranking/1.0")
    properties = schema["properties"]
    forbidden = {
        "final_selection",
        "final_selection_performed",
        "selected_candidate",
        "selected_candidate_id",
        "automatic_selection",
        "winner",
        "chosen_file",
    }
    assert forbidden.isdisjoint(properties.keys())
    # Nested properties must also stay free of auto-selection result fields.
    dumped = json.dumps(schema)
    for name in ("final_selection", "selected_candidate_id", "automatic_selection"):
        assert name not in dumped


def test_publish_metadata_draft_has_no_execution_or_account_fields() -> None:
    registry = ArtifactSchemaRegistry(SCHEMAS)
    schema = registry.schema_for_version("publish-metadata-draft/1.0")
    properties = schema["properties"]
    forbidden = {
        "account_id",
        "channel_account",
        "auto_upload",
        "auto_publish",
        "upload_performed",
        "publish_performed",
        "title_change_performed",
        "upload_now",
        "credentials",
    }
    assert forbidden.isdisjoint(properties.keys())
    document = _valid_publish_metadata_draft()
    assert document["human_review_required"] is True
    result = validate_artifact(document)
    assert result.ok is True, result.error_texts


def test_batch_runner_counts_all_production_artifact_families(
    tmp_path: Path,
) -> None:
    for version in ARTIFACT_VERSIONS:
        safe = version.replace("/", "_")
        path = tmp_path / f"{safe}.json"
        path.write_text(
            json.dumps(VALID_BY_VERSION[version]()),
            encoding="utf-8",
        )
    bad = tmp_path / "bad_packet.json"
    bad.write_text(
        json.dumps(
            {
                "artifact_version": "generation-packet/1.0",
                "rules_version": "x",
                "episode_id": "ep-1",
            }
        ),
        encoding="utf-8",
    )
    report = validate_artifact_directory(tmp_path)
    assert report.total == PRODUCTION_ARTIFACT_COUNT + 1
    assert report.passed == PRODUCTION_ARTIFACT_COUNT
    assert report.failed == 1
    assert report.skipped == 0


def test_external_call_reservation_is_record_only_without_trigger_fields() -> None:
    registry = ArtifactSchemaRegistry(SCHEMAS)
    schema = registry.schema_for_version("external-call-reservation/1.0")
    properties = schema["properties"]
    forbidden = {
        "auto_dispatch",
        "trigger_now",
        "execute",
        "call_now",
        "invoke",
    }
    assert forbidden.isdisjoint(properties.keys())
    result = validate_artifact(_valid_external_call_reservation())
    assert result.ok is True, result.error_texts


def test_shot_qc_aligns_with_qc_contract_shape() -> None:
    document = _valid_shot_qc()
    result = validate_artifact(document)
    assert result.ok is True, result.error_texts
    finding = document["findings"][0]
    assert set(finding) >= {"constraint_id", "passed", "observed", "message"}
    constraint = document["constraints"][0]
    assert set(constraint) >= {
        "constraint_id",
        "measurement_id",
        "comparison",
        "operands",
        "severity",
    }


def test_handoff_result_outcome_matches_adapter_envelope() -> None:
    document = _valid_handoff_result()
    assert document["outcome"] == "SUCCEEDED"
    result = validate_artifact(document)
    assert result.ok is True, result.error_texts
    bad = deepcopy(document)
    bad["outcome"] = "OK"
    assert validate_artifact(bad).ok is False
