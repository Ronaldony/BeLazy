"""CLI plan-command promotion: documents returned, filesystem unchanged."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from video_factory.approvals import (
    ApprovalEvidence,
    ApprovalState,
    GateContext,
    build_approval_requirement,
)
from video_factory.cli import (
    ImplementationStatus,
    handle_approve,
    handle_new_channel,
    handle_new_concept,
    handle_new_episode,
    handle_qc,
    handle_review,
    handle_run,
    handle_status,
    list_commands,
)
from video_factory.engine import make_artifact_snapshot
from video_factory.domain import HashDigest, OpaqueId, RoleId


def _snapshot(root: Path) -> dict[str, tuple[int, str]]:
    """Map relative path -> (size, mtime_ns) for files under root."""

    out: dict[str, tuple[int, str]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(root))
            stat = path.stat()
            out[rel] = (stat.st_size, f"{stat.st_mtime_ns}")
    return out


def _gate_context() -> GateContext:
    return GateContext(
        workflow_definition_sha256=HashDigest("1" * 64),
        policy_bundle_sha256=HashDigest("2" * 64),
        rules_bundle_sha256=HashDigest("3" * 64),
        effective_config_sha256=HashDigest("b" * 64),
        current_manifest_sha256=HashDigest("4" * 64),
        evidence_graph_sha256=HashDigest("5" * 64),
        executable_plan_sha256=HashDigest("6" * 64),
    )


def test_registry_statuses_after_phase20() -> None:
    by_name = {spec.name: spec for spec in list_commands()}
    assert len(by_name) == 17
    implemented = {
        "init",
        "export",
        "doctor",
        "validate",
        "qc",
        "approve",
        "run",
        "status",
        "new-channel",
        "new-concept",
        "new-episode",
        "review",
    }
    contract_only = {"retry", "resume", "invalidate", "reopen"}
    for name in implemented:
        assert by_name[name].implementation_status is ImplementationStatus.IMPLEMENTED
    for name in contract_only:
        assert by_name[name].implementation_status is ImplementationStatus.CONTRACT_ONLY
    assert by_name["migrate"].implementation_status is ImplementationStatus.NOT_YET_BACKED
    # Only migrate remains not_yet_backed.
    not_backed = [
        s.name
        for s in list_commands()
        if s.implementation_status is ImplementationStatus.NOT_YET_BACKED
    ]
    assert not_backed == ["migrate"]


def test_promoted_handlers_return_plans_without_filesystem_change(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "workspace"
    marker.mkdir()
    sample = marker / "keep.txt"
    sample.write_text("unchanged\n", encoding="utf-8")
    before = _snapshot(marker)

    expectations = [
        {
            "measurement_id": "duration_seconds",
            "comparison": "range",
            "operands": [30, 45],
            "constraint_id": "c-dur",
        }
    ]
    qc = handle_qc(
        "standard",
        expectations=expectations,
        measurements=[{"measurement_id": "duration_seconds", "value": 36}],
    )
    assert qc.exit_code == 0
    assert qc.payload is not None
    assert qc.payload["executed"] is False
    assert qc.payload["judgment"] is not None

    approve = handle_approve(
        "standard",
        kind="packet",
        artifacts=[
            {
                "path": "packets/p.json",
                "sha256": "a" * 64,
                "artifact_version": "generation-packet/1.0",
            }
        ],
        effective_config_sha256="b" * 64,
        requirement_id="req-cli-1",
    )
    assert approve.exit_code == 0
    assert approve.payload is not None
    assert approve.payload["creates_evidence"] is False

    brief = make_artifact_snapshot(
        "01_brief/brief.json",
        {
            "artifact_version": "brief/1.0",
            "episode_id": "e1",
            "rules_version": "rules-test",
            "summary": "summary",
            "hook": "hook",
            "development": "development",
            "ending": "ending",
            "risks": [],
        },
    )
    status = handle_status("standard", artifact_docs=[brief])
    assert status.exit_code == 0
    assert status.payload is not None
    assert status.payload["mutated"] is False

    run = handle_run("standard", artifact_docs=[brief])
    assert run.exit_code == 0
    assert run.payload is not None
    assert run.payload["transition_applied"] is False
    assert run.payload["executed"] is False

    ch = handle_new_channel("rapid", channel_id="channel-draft-1")
    assert ch.exit_code == 0
    assert ch.payload is not None
    assert ch.payload["written"] is False
    assert ch.payload["document"]["channel_id"] == "channel-draft-1"

    co = handle_new_concept("rapid", concept_id="concept-draft-1")
    assert co.exit_code == 0
    assert co.payload is not None
    assert co.payload["written"] is False

    ep = handle_new_episode("rapid", episode_id="episode-draft-1")
    assert ep.exit_code == 0
    assert ep.payload is not None
    assert ep.payload["written"] is False

    review = handle_review(
        "standard",
        review_id="rev-1",
        subject={
            "path": "storyboards/s.json",
            "sha256": "c" * 64,
            "artifact_version": "storyboard/1.0",
        },
        creator_role="creator",
        reviewer_role="reviewer",
        effective_config_sha256="d" * 64,
    )
    assert review.exit_code == 0
    assert review.payload is not None
    assert review.payload["performed"] is False

    after = _snapshot(marker)
    assert after == before
    assert sample.read_text(encoding="utf-8") == "unchanged\n"


def test_approve_handler_requires_current_context_for_evidence_binding() -> None:
    artifact = {
        "path": "packets/p.json",
        "sha256": "a" * 64,
        "artifact_version": "generation-packet/1.0",
    }
    context = _gate_context()
    requirement = build_approval_requirement(
        "packet",
        [artifact],
        "b" * 64,
        gate_context=context,
    )
    evidence = ApprovalEvidence(
        evidence_id=OpaqueId("evidence-cli"),
        requirement=requirement,
        state=ApprovalState.GRANTED,
        approver_role=RoleId("human-operator"),
        created_at="2026-07-21T00:00:00Z",
        record_sha256=HashDigest("c" * 64),
        expires_at="2026-07-21T02:00:00Z",
    )
    accepted = handle_approve(
        "standard",
        kind="packet",
        artifacts=[artifact],
        effective_config_sha256="b" * 64,
        evidence=evidence,
        gate_context=context,
        evaluated_at=datetime(2026, 7, 21, 1, 0, tzinfo=timezone.utc),
    )
    assert accepted.status == "ok"

    missing = handle_approve(
        "standard",
        kind="packet",
        artifacts=[artifact],
        effective_config_sha256="b" * 64,
        evidence=evidence,
    )
    assert missing.status == "evidence_rejected"
    assert missing.payload["evidence_binding"]["reason_code"] == (
        "approval.current_context_missing"
    )

    stale = replace(
        context, workflow_definition_sha256=HashDigest("e" * 64)
    )
    rejected = handle_approve(
        "standard",
        kind="packet",
        artifacts=[artifact],
        effective_config_sha256="b" * 64,
        evidence=evidence,
        gate_context=stale,
        evaluated_at="2026-07-21T01:00:00Z",
    )
    assert rejected.status == "evidence_rejected"


def test_mode_required_for_promoted_commands() -> None:
    for handler, kwargs in (
        (handle_status, {"artifact_docs": []}),
        (handle_run, {"artifact_docs": []}),
        (handle_new_channel, {"channel_id": "x"}),
        (
            handle_qc,
            {
                "expectations": [
                    {
                        "measurement_id": "fps",
                        "comparison": "minimum",
                        "operands": [24],
                    }
                ]
            },
        ),
    ):
        result = handler(None, **kwargs)  # type: ignore[operator]
        assert result.exit_code == 2
        assert result.status == "policy_error"
