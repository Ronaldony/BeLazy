"""Strict structural parsing for non-authorizing CandidateDecision documents."""

from __future__ import annotations

from collections.abc import Mapping

from video_factory.approvals import gate_context_from_mapping
from video_factory.artifacts import validate_artifact_mapping
from video_factory.config import canonical_json_bytes
from video_factory.domain import (
    ArtifactReference,
    ArtifactVersion,
    HashDigest,
    OpaqueId,
    RelativeArtifactPath,
)
from video_factory.json_boundary import (
    JsonInputError,
    parse_json_bytes,
    require_json_object,
    validate_json_mapping,
)
from video_factory.quality import MediaSubject

from .contracts import (
    CandidateDecision,
    CandidateDecisionStatus,
    RankedCandidate,
    ShotCandidateDecision,
)
from .decision import (
    CANDIDATE_DECISION_VERSION,
    candidate_decision_to_mapping,
    validate_candidate_decision_structure,
)


class SelectionSerializationError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _reference(value: object) -> ArtifactReference:
    assert isinstance(value, Mapping)
    return ArtifactReference(
        RelativeArtifactPath(str(value["path"])),
        HashDigest(str(value["sha256"])),
        ArtifactVersion(str(value["artifact_version"])),
    )


def _media(value: object) -> MediaSubject:
    assert isinstance(value, Mapping)
    return MediaSubject(
        shot_id=OpaqueId(str(value["shot_id"])),
        component_id=OpaqueId(str(value["component_id"])),
        reference=_reference(value["reference"]),
        byte_length=int(value["byte_length"]),
        workspace_observation_sha256=HashDigest(
            str(value["workspace_observation_sha256"])
        ),
        observation_receipt_ref=_reference(value["observation_receipt_ref"]),
    )


def _ranked(value: object) -> RankedCandidate:
    assert isinstance(value, Mapping)
    return RankedCandidate(
        subject=_media(value["subject"]),
        adapter_id=OpaqueId(str(value["adapter_id"])),
        quality_score_bps=int(value["quality_score_bps"]),
        confidence_bps=int(value["confidence_bps"]),
        confidence_receipt_ref=_reference(value["confidence_receipt_ref"]),
    )


def _shot(value: object) -> ShotCandidateDecision:
    assert isinstance(value, Mapping)
    selected = value["selected_subject"]
    return ShotCandidateDecision(
        shot_id=OpaqueId(str(value["shot_id"])),
        status=CandidateDecisionStatus(str(value["status"])),
        ranked_candidates=tuple(
            _ranked(item) for item in value["ranked_candidates"]
        ),
        selected_subject=_media(selected) if selected is not None else None,
        margin_to_second_bps=int(value["margin_to_second_bps"]),
        reason_codes=tuple(str(item) for item in value["reason_codes"]),
    )


def candidate_decision_from_mapping(
    document: Mapping[str, object],
) -> CandidateDecision:
    try:
        value = dict(validate_json_mapping(document))
    except JsonInputError as error:
        raise SelectionSerializationError(error.code.value, str(error)) from error
    if value.get("artifact_version") != CANDIDATE_DECISION_VERSION:
        raise SelectionSerializationError(
            "selection.serialization.version",
            "unsupported candidate decision version",
        )
    result = validate_artifact_mapping(value)
    if not result.ok:
        raise SelectionSerializationError(
            "selection.serialization.schema",
            "; ".join(result.error_texts),
        )
    context = value["gate_context"]
    assert isinstance(context, Mapping)
    request_sha = value["authority_request_sha256"]
    risk_sha = value["authority_risk_sha256"]
    decision_sha = value["authority_decision_sha256"]
    receipt_sha = value["authority_receipt_sha256"]
    decision = CandidateDecision(
        artifact_version=str(value["artifact_version"]),
        decision_id=OpaqueId(str(value["decision_id"])),
        decision_sha256=HashDigest(str(value["decision_sha256"])),
        workspace_id=OpaqueId(str(value["workspace_id"])),
        channel_id=OpaqueId(str(value["channel_id"])),
        concept_id=OpaqueId(str(value["concept_id"])),
        episode_id=OpaqueId(str(value["episode_id"])),
        quality_bundle_ref=_reference(value["quality_bundle_ref"]),
        quality_bundle_sha256=HashDigest(str(value["quality_bundle_sha256"])),
        policy_sha256=HashDigest(str(value["policy_sha256"])),
        gate_context=gate_context_from_mapping(context),
        evaluated_at=str(value["evaluated_at"]),
        selection_input_sha256=HashDigest(str(value["selection_input_sha256"])),
        authority_request_sha256=(
            HashDigest(str(request_sha)) if request_sha is not None else None
        ),
        authority_risk_sha256=(
            HashDigest(str(risk_sha)) if risk_sha is not None else None
        ),
        authority_decision_sha256=(
            HashDigest(str(decision_sha)) if decision_sha is not None else None
        ),
        authority_receipt_sha256=(
            HashDigest(str(receipt_sha)) if receipt_sha is not None else None
        ),
        shots=tuple(_shot(item) for item in value["shots"]),
        status=CandidateDecisionStatus(str(value["status"])),
        reason_codes=tuple(str(item) for item in value["reason_codes"]),
        authority_effect=str(value["authority_effect"]),
    )
    return validate_candidate_decision_structure(decision)


def candidate_decision_from_bytes(payload: bytes) -> CandidateDecision:
    try:
        document = require_json_object(parse_json_bytes(payload))
    except JsonInputError as error:
        raise SelectionSerializationError(error.code.value, str(error)) from error
    return candidate_decision_from_mapping(document)


def candidate_decision_to_bytes(value: CandidateDecision) -> bytes:
    return canonical_json_bytes(candidate_decision_to_mapping(value))


__all__ = [
    "SelectionSerializationError",
    "candidate_decision_from_bytes",
    "candidate_decision_from_mapping",
    "candidate_decision_to_bytes",
]
