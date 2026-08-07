"""Approval requirement builder and evidence-binding checks."""

from __future__ import annotations

import pytest

from video_factory.approvals import (
    ApprovalEvidence,
    ApprovalRequirement,
    ApprovalRequirementError,
    ApprovalState,
    approval_evidence_to_mapping,
    build_approval_requirement,
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
    req = build_approval_requirement(
        "packet",
        [_artifact()],
        _CONFIG,
        requirement_id="req-bind-1",
        capability_id="generation_approval",
    )
    good = ApprovalEvidence(
        evidence_id=OpaqueId("ev-1"),
        requirement=req,
        state=ApprovalState.GRANTED,
        approver_role=RoleId("human-operator"),
        created_at="2026-07-21T00:00:00Z",
        record_sha256=HashDigest(_HASH_B),
    )
    ok = validate_evidence_binding(req, good)
    assert ok.ok is True
    assert ok.evidence_id == "ev-1"

    # config hash mismatch
    bad_config_req = ApprovalRequirement(
        requirement_id=req.requirement_id,
        capability_id=req.capability_id,
        bound_artifacts=req.bound_artifacts,
        effective_config_sha256=HashDigest(_CONFIG_OTHER),
    )
    bad_config_ev = ApprovalEvidence(
        evidence_id=OpaqueId("ev-2"),
        requirement=bad_config_req,
        state=ApprovalState.GRANTED,
        approver_role=RoleId("human-operator"),
        created_at="2026-07-21T00:00:00Z",
        record_sha256=HashDigest(_HASH_B),
    )
    rejected = validate_evidence_binding(req, bad_config_ev)
    assert rejected.ok is False
    assert "effective config" in rejected.message

    # artifact mismatch
    other_art = ApprovalRequirement(
        requirement_id=req.requirement_id,
        capability_id=req.capability_id,
        bound_artifacts=(_artifact("other.json", _HASH_B),),
        effective_config_sha256=req.effective_config_sha256,
    )
    bad_art_ev = ApprovalEvidence(
        evidence_id=OpaqueId("ev-3"),
        requirement=other_art,
        state=ApprovalState.GRANTED,
        approver_role=RoleId("human-operator"),
        created_at="2026-07-21T00:00:00Z",
        record_sha256=HashDigest(_HASH_B),
    )
    rejected_art = validate_evidence_binding(req, bad_art_ev)
    assert rejected_art.ok is False
    assert "different input artifacts" in rejected_art.message

    # missing evidence
    missing = validate_evidence_binding(req, None)
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
