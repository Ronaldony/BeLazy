"""Deterministic generation order-sheet renderer tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from video_factory.approvals import GateContext, gate_context_sha256
from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    HashDigest,
    RelativeArtifactPath,
)
from video_factory.engine import GenerationReadinessPlan, OrchestrationPlanError
from video_factory.sheets import (
    GenerationSheetError,
    packet_document_sha256,
    render_generation_sheet,
)


def _context() -> GateContext:
    return GateContext(
        workflow_definition_sha256=HashDigest("1" * 64),
        policy_bundle_sha256=HashDigest("2" * 64),
        rules_bundle_sha256=HashDigest("3" * 64),
        effective_config_sha256=HashDigest("4" * 64),
        current_manifest_sha256=HashDigest("5" * 64),
        evidence_graph_sha256=HashDigest("6" * 64),
        executable_plan_sha256=HashDigest("7" * 64),
    )


def _sample_packet() -> dict[str, object]:
    return {
        "artifact_version": "generation-packet/1.0",
        "rules_version": "rules-bundle-test",
        "episode_id": "ep-demo-001",
        "generated_by": "role:writer",
        "approved_by_human": True,
        "title_working": "Demo Device",
        "output": {
            "dir": "06_raw",
            "filename_pattern": "{episode_id}_{shot}_{version}.mp4",
            "target": {"aspect": "portrait", "min_height": 1080, "fps_min": 24},
        },
        "quota_plan": {
            "date": "2026-07-21",
            "budgets": {"adapter-a": {"unit": "credits", "amount": 3}},
        },
        "shots": [
            {
                "shot_id": "shot-01",
                "duration_sec": 6,
                "duration_tolerance_sec": 1,
                "candidates": 2,
                "mode": "image_to_video",
                "prompt": "SUBJECT\nexact block one\n\nACTION\ndo the thing\n",
                "first_frame_note": "wide establishing",
                "checklist": ["no extra limbs", "stable product"],
                "provider_plans": [
                    {"adapter_id": "adapter-b", "candidates": 1},
                    {"adapter_id": "adapter-a", "candidates": 1},
                ],
                "reference_assets": [
                    {"path": "05_references/start.png", "sha256": "a" * 64}
                ],
            },
            {
                "shot_id": "shot-02",
                "duration_sec": 5.5,
                "candidates": 1,
                "prompt": "ENVIRONMENT\nroom description that must not change\n",
            },
        ],
    }


def test_renderer_determinism_byte_identical() -> None:
    packet = _sample_packet()
    a = render_generation_sheet(packet)
    b = render_generation_sheet(packet)
    assert a == b
    assert a.encode("utf-8") == b.encode("utf-8")


def test_prompt_blocks_pass_through_unmodified() -> None:
    packet = _sample_packet()
    sheet = render_generation_sheet(packet)
    for shot in packet["shots"]:
        prompt = shot["prompt"]  # type: ignore[index]
        assert isinstance(prompt, str)
        # Fenced block must contain the exact prompt body on its own lines
        assert f"```\n{prompt}\n```" in sheet


def test_packet_sha256_header_tracks_document() -> None:
    packet = _sample_packet()
    digest = packet_document_sha256(packet)
    sheet = render_generation_sheet(packet)
    assert f"packet_sha256: `{digest}`" in sheet
    assert f"<!-- packet_sha256:{digest} -->" in sheet

    changed = dict(packet)
    changed["title_working"] = "Changed"
    sheet2 = render_generation_sheet(changed)
    digest2 = packet_document_sha256(changed)
    assert digest != digest2
    assert f"packet_sha256: `{digest2}`" in sheet2
    assert digest not in sheet2 or digest2 in sheet2


def test_provider_plans_sorted_for_stability() -> None:
    sheet = render_generation_sheet(_sample_packet())
    # adapter-a before adapter-b regardless of input order
    assert "provider_plans: adapter-a x1, adapter-b x1" in sheet


def _readiness(packet: dict[str, object]) -> GenerationReadinessPlan:
    packet_sha = packet_document_sha256(packet)
    return GenerationReadinessPlan(
        ready=True,
        packet=ArtifactReference(
            path=RelativeArtifactPath("04_prompts/packet.json"),
            sha256=HashDigest("a" * 64),
            artifact_version=ArtifactVersion("generation-packet/2.0"),
        ),
        packet_content_sha256=packet_sha,
        feasibility_review=ArtifactReference(
            path=RelativeArtifactPath("04_prompts/feasibility.json"),
            sha256=HashDigest("b" * 64),
            artifact_version=ArtifactVersion("generation-feasibility-review/1.0"),
        ),
        approval_evidence=ArtifactReference(
            path=RelativeArtifactPath("04_prompts/packet_approval.json"),
            sha256=HashDigest("c" * 64),
            artifact_version=ArtifactVersion("packet-approval/2.0"),
        ),
        blockers=(),
        gate_context_sha256=str(gate_context_sha256(_context())),
        authorization_ready=True,
        valid_from="2026-07-21T00:00:00Z",
        valid_until="2026-07-21T02:00:00Z",
    )


def test_packet_flag_never_authorizes_generation() -> None:
    packet = _sample_packet()
    sheet = render_generation_sheet(packet)
    assert "DO NOT GENERATE" in sheet
    assert "generation_readiness: `blocked`" in sheet


def test_bound_readiness_authorizes_sheet() -> None:
    packet = _sample_packet()
    sheet = render_generation_sheet(
        packet,
        readiness=_readiness(packet),
        current_context=_context(),
        evaluated_at=datetime(2026, 7, 21, 1, 0, tzinfo=timezone.utc),
    )
    assert "DO NOT GENERATE" not in sheet
    assert "generation_readiness: `ready`" in sheet
    assert "feasibility_evidence:" in sheet
    assert "approval_evidence:" in sheet


def test_stale_readiness_is_rejected() -> None:
    packet = _sample_packet()
    readiness = _readiness(packet)
    changed = dict(packet)
    changed["title_working"] = "changed after approval"
    with pytest.raises(GenerationSheetError, match="different packet"):
        render_generation_sheet(changed, readiness=readiness)


def test_context_free_legacy_readiness_is_preview_only() -> None:
    packet = _sample_packet()
    legacy = replace(
        _readiness(packet),
        authorization_ready=False,
        gate_context_sha256=None,
        valid_from=None,
        valid_until=None,
    )
    sheet = render_generation_sheet(packet, readiness=legacy)
    assert "DO NOT GENERATE" in sheet
    assert "generation_readiness: `blocked`" in sheet


@pytest.mark.parametrize(
    "field",
    [
        "workflow_definition_sha256",
        "policy_bundle_sha256",
        "rules_bundle_sha256",
        "effective_config_sha256",
        "current_manifest_sha256",
        "evidence_graph_sha256",
        "executable_plan_sha256",
    ],
)
def test_sheet_rejects_every_current_context_digest_change(field: str) -> None:
    packet = _sample_packet()
    changed = replace(_context(), **{field: HashDigest("e" * 64)})
    with pytest.raises(GenerationSheetError, match="another gate context"):
        render_generation_sheet(
            packet,
            readiness=_readiness(packet),
            current_context=changed,
            evaluated_at=datetime(2026, 7, 21, 1, 0, tzinfo=timezone.utc),
        )


@pytest.mark.parametrize(
    ("evaluated_at", "message"),
    [
        (None, "timezone-aware"),
        (datetime(2026, 7, 21, 1, 0), "timezone-aware"),
        (datetime(2026, 7, 20, 23, 59, tzinfo=timezone.utc), "not yet valid"),
        (datetime(2026, 7, 21, 2, 0, tzinfo=timezone.utc), "expired"),
    ],
)
def test_sheet_rechecks_readiness_validity_window(
    evaluated_at: datetime | None,
    message: str,
) -> None:
    with pytest.raises(GenerationSheetError, match=message):
        render_generation_sheet(
            _sample_packet(),
            readiness=_readiness(_sample_packet()),
            current_context=_context(),
            evaluated_at=evaluated_at,
        )


def test_authorization_ready_plan_rejects_internal_inconsistency() -> None:
    with pytest.raises(OrchestrationPlanError, match="cannot contain blockers"):
        replace(_readiness(_sample_packet()), blockers=("stale",))
