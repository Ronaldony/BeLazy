"""Strict structural parsing for non-publishing W05 release artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeAlias

from video_factory.approvals import gate_context_from_mapping
from video_factory.artifacts import validate_artifact_mapping
from video_factory.authority import approval_request_from_mapping
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
    DestinationBinding,
    ReleaseAssessment,
    ReleaseAssessmentStatus,
    ReleaseCandidate,
    ReleaseVisibility,
)
from .decision import (
    DESTINATION_BINDING_VERSION,
    RELEASE_ASSESSMENT_VERSION,
    RELEASE_CANDIDATE_VERSION,
    destination_binding_to_mapping,
    release_assessment_to_mapping,
    release_candidate_to_mapping,
    validate_destination_binding,
    validate_release_assessment_structure,
    validate_release_candidate_structure,
)


ReleaseArtifact: TypeAlias = DestinationBinding | ReleaseCandidate | ReleaseAssessment


class ReleaseSerializationError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _schema_validated(
    document: Mapping[str, object],
    version: str,
) -> dict[str, object]:
    try:
        value = dict(validate_json_mapping(document))
    except JsonInputError as error:
        raise ReleaseSerializationError(error.code.value, str(error)) from error
    if value.get("artifact_version") != version:
        raise ReleaseSerializationError(
            "release.serialization.version",
            f"expected {version!r}",
        )
    result = validate_artifact_mapping(value)
    if not result.ok:
        raise ReleaseSerializationError(
            "release.serialization.schema",
            "; ".join(result.error_texts),
        )
    return value


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


def destination_binding_from_mapping(
    document: Mapping[str, object],
) -> DestinationBinding:
    value = _schema_validated(document, DESTINATION_BINDING_VERSION)
    destination = DestinationBinding(
        artifact_version=str(value["artifact_version"]),
        destination_id=OpaqueId(str(value["destination_id"])),
        destination_sha256=HashDigest(str(value["destination_sha256"])),
        platform=str(value["platform"]),
        channel_account_id=OpaqueId(str(value["channel_account_id"])),
        visibility=ReleaseVisibility(str(value["visibility"])),
        locale=str(value["locale"]),
        region=str(value["region"]),
        scheduled_for=(
            str(value["scheduled_for"])
            if value["scheduled_for"] is not None
            else None
        ),
        policy_sha256=HashDigest(str(value["policy_sha256"])),
        authority_effect=str(value["authority_effect"]),
    )
    return validate_destination_binding(destination)


def release_candidate_from_mapping(
    document: Mapping[str, object],
) -> ReleaseCandidate:
    value = _schema_validated(document, RELEASE_CANDIDATE_VERSION)
    context = value["gate_context"]
    assert isinstance(context, Mapping)
    candidate = ReleaseCandidate(
        artifact_version=str(value["artifact_version"]),
        candidate_id=OpaqueId(str(value["candidate_id"])),
        candidate_sha256=HashDigest(str(value["candidate_sha256"])),
        workspace_id=OpaqueId(str(value["workspace_id"])),
        channel_id=OpaqueId(str(value["channel_id"])),
        concept_id=OpaqueId(str(value["concept_id"])),
        episode_id=OpaqueId(str(value["episode_id"])),
        final_media=_media(value["final_media"]),
        metadata_ref=_reference(value["metadata_ref"]),
        subtitle_accessibility_refs=tuple(
            _reference(item) for item in value["subtitle_accessibility_refs"]
        ),
        thumbnail=_media(value["thumbnail"]),
        quality_bundle_ref=_reference(value["quality_bundle_ref"]),
        quality_bundle_sha256=HashDigest(str(value["quality_bundle_sha256"])),
        candidate_decision_ref=_reference(value["candidate_decision_ref"]),
        candidate_decision_sha256=HashDigest(
            str(value["candidate_decision_sha256"])
        ),
        destination_ref=_reference(value["destination_ref"]),
        destination_sha256=HashDigest(str(value["destination_sha256"])),
        policy_sha256=HashDigest(str(value["policy_sha256"])),
        gate_context=gate_context_from_mapping(context),
        workspace_observation_sha256=HashDigest(
            str(value["workspace_observation_sha256"])
        ),
        release_intent_sha256=HashDigest(str(value["release_intent_sha256"])),
        created_at=str(value["created_at"]),
        authority_effect=str(value["authority_effect"]),
    )
    return validate_release_candidate_structure(candidate)


def release_assessment_from_mapping(
    document: Mapping[str, object],
) -> ReleaseAssessment:
    value = _schema_validated(document, RELEASE_ASSESSMENT_VERSION)
    context = value["gate_context"]
    assert isinstance(context, Mapping)
    approval = value["approval_request"]
    assessment = ReleaseAssessment(
        artifact_version=str(value["artifact_version"]),
        assessment_id=OpaqueId(str(value["assessment_id"])),
        assessment_sha256=HashDigest(str(value["assessment_sha256"])),
        release_candidate_ref=_reference(value["release_candidate_ref"]),
        release_candidate_sha256=HashDigest(
            str(value["release_candidate_sha256"])
        ),
        gate_context=gate_context_from_mapping(context),
        destination_sha256=HashDigest(str(value["destination_sha256"])),
        evaluated_at=str(value["evaluated_at"]),
        authority_request_sha256=(
            HashDigest(str(value["authority_request_sha256"]))
            if value["authority_request_sha256"] is not None
            else None
        ),
        risk_assessment_sha256=(
            HashDigest(str(value["risk_assessment_sha256"]))
            if value["risk_assessment_sha256"] is not None
            else None
        ),
        authority_decision_sha256=(
            HashDigest(str(value["authority_decision_sha256"]))
            if value["authority_decision_sha256"] is not None
            else None
        ),
        authority_receipt_sha256=(
            HashDigest(str(value["authority_receipt_sha256"]))
            if value["authority_receipt_sha256"] is not None
            else None
        ),
        approval_request=(
            approval_request_from_mapping(approval)
            if isinstance(approval, Mapping)
            else None
        ),
        status=ReleaseAssessmentStatus(str(value["status"])),
        reason_codes=tuple(str(item) for item in value["reason_codes"]),
        required_independent_humans=int(value["required_independent_humans"]),
        publish_performed=bool(value["publish_performed"]),
        authority_effect=str(value["authority_effect"]),
    )
    return validate_release_assessment_structure(assessment)


def release_artifact_from_mapping(
    document: Mapping[str, object],
) -> ReleaseArtifact:
    version = document.get("artifact_version")
    if version == DESTINATION_BINDING_VERSION:
        return destination_binding_from_mapping(document)
    if version == RELEASE_CANDIDATE_VERSION:
        return release_candidate_from_mapping(document)
    if version == RELEASE_ASSESSMENT_VERSION:
        return release_assessment_from_mapping(document)
    raise ReleaseSerializationError(
        "release.serialization.version",
        "unsupported W05 release artifact version",
    )


def release_artifact_from_bytes(payload: bytes) -> ReleaseArtifact:
    try:
        document = require_json_object(parse_json_bytes(payload))
    except JsonInputError as error:
        raise ReleaseSerializationError(error.code.value, str(error)) from error
    return release_artifact_from_mapping(document)


def release_artifact_to_mapping(value: ReleaseArtifact) -> dict[str, object]:
    if isinstance(value, DestinationBinding):
        return destination_binding_to_mapping(value)
    if isinstance(value, ReleaseCandidate):
        return release_candidate_to_mapping(value)
    if isinstance(value, ReleaseAssessment):
        return release_assessment_to_mapping(value)
    raise TypeError(f"unsupported release artifact: {type(value)!r}")


def release_artifact_to_bytes(value: ReleaseArtifact) -> bytes:
    return canonical_json_bytes(release_artifact_to_mapping(value))


__all__ = [
    "ReleaseArtifact",
    "ReleaseSerializationError",
    "destination_binding_from_mapping",
    "release_artifact_from_bytes",
    "release_artifact_from_mapping",
    "release_artifact_to_bytes",
    "release_artifact_to_mapping",
    "release_assessment_from_mapping",
    "release_candidate_from_mapping",
]
