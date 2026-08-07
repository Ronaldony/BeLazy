"""Approval requirement builder and evidence-binding checks."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from video_factory.approvals import (
    ApprovalEvidence,
    ApprovalRequirement,
    ApprovalRequirementError,
    ApprovalState,
    GateContext,
    approval_evidence_to_mapping,
    assert_evidence_binding,
    build_approval_requirement,
    gate_context_to_mapping,
    requirement_from_mapping,
    requirement_to_mapping,
    validate_evidence_binding,
)
from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    HashDigest,
    OpaqueId,
    RelativeArtifactPath,
    RoleId,
)
from video_factory.artifacts import validate_artifact

_HASH_A = "a" * 64
_HASH_B = "b" * 64
_CONFIG = "c" * 64
_CONFIG_OTHER = "d" * 64


def _context(config: str = _CONFIG) -> GateContext:
    return GateContext(
        workflow_definition_sha256=HashDigest("1" * 64),
        policy_bundle_sha256=HashDigest("2" * 64),
        rules_bundle_sha256=HashDigest("3" * 64),
        effective_config_sha256=HashDigest(config),
        current_manifest_sha256=HashDigest("4" * 64),
        evidence_graph_sha256=HashDigest("5" * 64),
        executable_plan_sha256=HashDigest("6" * 64),
    )


def _evidence(
    requirement: ApprovalRequirement,
    *,
    created_at: str = "2026-07-21T00:00:00Z",
    expires_at: str | None = "2026-07-21T02:00:00Z",
) -> ApprovalEvidence:
    return ApprovalEvidence(
        evidence_id=OpaqueId("evidence-context"),
        requirement=requirement,
        state=ApprovalState.GRANTED,
        approver_role=RoleId("human-operator"),
        created_at=created_at,
        record_sha256=HashDigest(_HASH_B),
        expires_at=expires_at,
    )


def _artifact(path: str = "packets/gen.json", digest: str = _HASH_A) -> ArtifactReference:
    return ArtifactReference(
        path=RelativeArtifactPath(path),
        sha256=HashDigest(digest),
        artifact_version=ArtifactVersion("generation-packet/1.0"),
    )


def test_build_requirement_never_returns_evidence() -> None:
    req = build_approval_requirement(
        "generation_approval",
        [_artifact()],
        _CONFIG,
        requirement_id="req-test-001",
    )
    assert isinstance(req, ApprovalRequirement)
    assert not isinstance(req, ApprovalEvidence)
    assert str(req.requirement_id) == "req-test-001"
    assert str(req.capability_id) == "generation_approval"
    assert req.bound_artifacts[0].sha256 == HashDigest(_HASH_A)
    mapping = requirement_to_mapping(
        req,
        kind="packet-approval",
        episode_id="ep-1",
        rules_version="rules-test",
    )
    assert mapping["creates_evidence"] is False
    assert mapping["artifact_version"] == "approval-requirement/1.0"
    assert "approved_by_human" not in mapping
    assert "approver_role" not in mapping
    assert "evidence_id" not in mapping
    assert validate_artifact(mapping).ok is True


def test_requirement_mapping_roundtrip() -> None:
    req = build_approval_requirement(
        "storyboard-approval",
        [_artifact("storyboards/s.json", _HASH_B)],
        _CONFIG,
        requirement_id="req-sb-1",
    )
    mapping = requirement_to_mapping(
        req,
        kind="storyboard-approval",
        episode_id="ep-1",
        rules_version="rules-test",
    )
    restored = requirement_from_mapping(mapping)
    assert restored.capability_id == req.capability_id
    assert restored.effective_config_sha256 == req.effective_config_sha256
    assert restored.bound_artifacts == req.bound_artifacts


def test_evidence_binding_match_and_mismatches() -> None:
    context = _context()
    evaluation = datetime(2026, 7, 21, 1, 0, tzinfo=timezone.utc)
    req = build_approval_requirement(
        "packet",
        [_artifact()],
        _CONFIG,
        requirement_id="req-bind-1",
        capability_id="generation_approval",
        gate_context=context,
    )
    good = ApprovalEvidence(
        evidence_id=OpaqueId("ev-1"),
        requirement=req,
        state=ApprovalState.GRANTED,
        approver_role=RoleId("human-operator"),
        created_at="2026-07-21T00:00:00Z",
        record_sha256=HashDigest(_HASH_B),
        expires_at="2026-07-21T02:00:00Z",
    )
    ok = validate_evidence_binding(
        req, good, current_context=context, evaluated_at=evaluation
    )
    assert ok.ok is True
    assert ok.evidence_id == "ev-1"

    # config hash mismatch
    bad_config_req = ApprovalRequirement(
        requirement_id=req.requirement_id,
        capability_id=req.capability_id,
        bound_artifacts=req.bound_artifacts,
        effective_config_sha256=HashDigest(_CONFIG_OTHER),
        gate_context=_context(_CONFIG_OTHER),
    )
    bad_config_ev = ApprovalEvidence(
        evidence_id=OpaqueId("ev-2"),
        requirement=bad_config_req,
        state=ApprovalState.GRANTED,
        approver_role=RoleId("human-operator"),
        created_at="2026-07-21T00:00:00Z",
        record_sha256=HashDigest(_HASH_B),
        expires_at="2026-07-21T02:00:00Z",
    )
    rejected = validate_evidence_binding(
        req, bad_config_ev, current_context=context, evaluated_at=evaluation
    )
    assert rejected.ok is False
    assert "effective config" in rejected.message

    # artifact mismatch
    other_art = ApprovalRequirement(
        requirement_id=req.requirement_id,
        capability_id=req.capability_id,
        bound_artifacts=(_artifact("other.json", _HASH_B),),
        effective_config_sha256=req.effective_config_sha256,
        gate_context=context,
    )
    bad_art_ev = ApprovalEvidence(
        evidence_id=OpaqueId("ev-3"),
        requirement=other_art,
        state=ApprovalState.GRANTED,
        approver_role=RoleId("human-operator"),
        created_at="2026-07-21T00:00:00Z",
        record_sha256=HashDigest(_HASH_B),
        expires_at="2026-07-21T02:00:00Z",
    )
    rejected_art = validate_evidence_binding(
        req, bad_art_ev, current_context=context, evaluated_at=evaluation
    )
    assert rejected_art.ok is False
    assert "different input artifacts" in rejected_art.message

    # missing evidence
    missing = validate_evidence_binding(
        req, None, current_context=context, evaluated_at=evaluation
    )
    assert missing.ok is False
    assert "missing" in missing.message


def test_empty_artifacts_rejected() -> None:
    with pytest.raises(ApprovalRequirementError, match="non-empty"):
        build_approval_requirement("packet", [], _CONFIG)


def test_default_requirement_id_is_deterministic() -> None:
    one = build_approval_requirement("packet", [_artifact()], _CONFIG)
    two = build_approval_requirement("packet", [_artifact()], _CONFIG)
    assert one.requirement_id == two.requirement_id


def test_granted_evidence_serializes_as_schema_valid_evidence() -> None:
    requirement = build_approval_requirement(
        "packet",
        [_artifact()],
        _CONFIG,
        requirement_id="req-evidence-1",
    )
    evidence = ApprovalEvidence(
        evidence_id=OpaqueId("evidence-1"),
        requirement=requirement,
        state=ApprovalState.GRANTED,
        approver_role=RoleId("human-operator"),
        created_at="2026-07-21T00:00:00Z",
        record_sha256=HashDigest(_HASH_B),
    )
    document = approval_evidence_to_mapping(
        evidence,
        kind="packet",
        episode_id="ep-1",
        rules_version="rules-test",
    )
    assert document["artifact_version"] == "packet-approval/2.0"
    assert document["state"] == "granted"
    assert validate_artifact(document).ok is True


def test_context_bound_requirement_roundtrip_and_schema_mapping() -> None:
    context = _context()
    requirement = build_approval_requirement(
        "packet",
        [_artifact()],
        _CONFIG,
        gate_context=context,
    )
    mapping = requirement_to_mapping(
        requirement,
        kind="packet-approval",
        episode_id="ep-1",
        rules_version="rules-test",
    )
    assert mapping["gate_context"] == gate_context_to_mapping(context)
    restored = requirement_from_mapping(mapping)
    assert restored == requirement
    assert validate_artifact(mapping).ok is True

    evidence = _evidence(requirement)
    evidence_mapping = approval_evidence_to_mapping(
        evidence,
        kind="packet",
        episode_id="ep-1",
        rules_version="rules-test",
    )
    assert evidence_mapping["gate_context"] == gate_context_to_mapping(context)
    assert evidence_mapping["expires_at"] == evidence.expires_at
    assert validate_artifact(evidence_mapping).ok is True


def test_current_context_and_validity_window_are_required_for_authorization() -> None:
    context = _context()
    requirement = build_approval_requirement(
        "packet", [_artifact()], _CONFIG, gate_context=context
    )
    evidence = _evidence(requirement)
    evaluation = datetime(2026, 7, 21, 1, 0, tzinfo=timezone.utc)
    accepted = validate_evidence_binding(
        requirement,
        evidence,
        current_context=context,
        evaluated_at=evaluation,
    )
    assert accepted.ok is True
    assert accepted.reason_code == "approval.binding_match"

    no_context = validate_evidence_binding(
        requirement, evidence, current_context=None, evaluated_at=evaluation
    )
    assert no_context.reason_code == "approval.current_context_missing"

    no_time = validate_evidence_binding(
        requirement, evidence, current_context=context, evaluated_at=None
    )
    assert no_time.reason_code == "approval.evaluation_time_missing"


def test_fully_legacy_evidence_is_loadable_but_cannot_authorize() -> None:
    requirement = build_approval_requirement("packet", [_artifact()], _CONFIG)
    evidence = ApprovalEvidence(
        evidence_id=OpaqueId("legacy-evidence"),
        requirement=requirement,
        state=ApprovalState.GRANTED,
        approver_role=RoleId("human-operator"),
        created_at="2026-07-21T00:00:00Z",
        record_sha256=HashDigest(_HASH_B),
    )
    result = validate_evidence_binding(
        requirement,
        evidence,
        current_context=None,
        evaluated_at=None,
    )
    assert result.ok is False
    assert result.reason_code == "approval.current_context_missing"
    with pytest.raises(ApprovalRequirementError, match="current gate context"):
        assert_evidence_binding(
            requirement,
            evidence,
            current_context=None,
            evaluated_at=None,
        )


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
def test_every_material_context_digest_mismatch_fails_closed(field: str) -> None:
    current = _context()
    expected = build_approval_requirement(
        "packet", [_artifact()], _CONFIG, gate_context=current
    )
    bad_context = replace(current, **{field: HashDigest("e" * 64)})
    bad_config = (
        str(bad_context.effective_config_sha256)
        if field == "effective_config_sha256"
        else _CONFIG
    )
    bound = ApprovalRequirement(
        requirement_id=expected.requirement_id,
        capability_id=expected.capability_id,
        bound_artifacts=expected.bound_artifacts,
        effective_config_sha256=HashDigest(bad_config),
        gate_context=bad_context,
    )
    result = validate_evidence_binding(
        expected,
        _evidence(bound),
        current_context=current,
        evaluated_at="2026-07-21T01:00:00Z",
    )
    assert result.ok is False
    assert result.reason_code in {
        "approval.effective_config_mismatch",
        "approval.context_mismatch",
    }


@pytest.mark.parametrize(
    ("created_at", "expires_at", "evaluated_at", "reason"),
    [
        ("2026-07-21T00:00:00Z", None, "2026-07-21T01:00:00Z", "approval.expiry_missing"),
        ("2026-07-21T00:00:00Z", "2026-07-21T01:00:00Z", "2026-07-21T01:00:00Z", "approval.expired"),
        ("2026-07-21T02:00:00Z", "2026-07-21T03:00:00Z", "2026-07-21T01:00:00Z", "approval.not_yet_valid"),
        ("not-a-date", "2026-07-21T03:00:00Z", "2026-07-21T01:00:00Z", "approval.validity_window_invalid"),
    ],
)
def test_expired_future_or_incomplete_evidence_fails_closed(
    created_at: str,
    expires_at: str | None,
    evaluated_at: str,
    reason: str,
) -> None:
    context = _context()
    requirement = build_approval_requirement(
        "packet", [_artifact()], _CONFIG, gate_context=context
    )
    result = validate_evidence_binding(
        requirement,
        _evidence(requirement, created_at=created_at, expires_at=expires_at),
        current_context=context,
        evaluated_at=evaluated_at,
    )
    assert result.ok is False
    assert result.reason_code == reason


def test_duplicate_bound_artifacts_cannot_collapse_to_a_set() -> None:
    context = _context()
    requirement = build_approval_requirement(
        "packet", [_artifact()], _CONFIG, gate_context=context
    )
    duplicated = ApprovalRequirement(
        requirement_id=requirement.requirement_id,
        capability_id=requirement.capability_id,
        bound_artifacts=(requirement.bound_artifacts[0], requirement.bound_artifacts[0]),
        effective_config_sha256=requirement.effective_config_sha256,
        gate_context=context,
    )
    result = validate_evidence_binding(
        requirement,
        _evidence(duplicated),
        current_context=context,
        evaluated_at="2026-07-21T01:00:00Z",
    )
    assert result.ok is False
    assert result.reason_code == "approval.artifact_duplicate"

    with pytest.raises(ApprovalRequirementError, match="duplicates"):
        build_approval_requirement(
            "packet", [_artifact(), _artifact()], _CONFIG, gate_context=context
        )


def test_requirement_identity_changes_with_material_context() -> None:
    one = build_approval_requirement(
        "packet", [_artifact()], _CONFIG, gate_context=_context()
    )
    changed = replace(
        _context(), workflow_definition_sha256=HashDigest("e" * 64)
    )
    two = build_approval_requirement(
        "packet", [_artifact()], _CONFIG, gate_context=changed
    )
    assert one.requirement_id != two.requirement_id
