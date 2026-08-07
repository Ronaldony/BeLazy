"""Strict JSON boundaries for registered Director Mesh artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from video_factory.artifacts import validate_artifact_mapping
from video_factory.config.canonical import canonical_json_bytes
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

from .contracts import (
    ConflictResult,
    ConflictStatus,
    DirectorAssessment,
    DirectorBlocker,
    DirectorCharter,
    DirectorKind,
    DirectorSynthesis,
    DirectorVerdict,
    PatchProposal,
    ProposalKind,
    SynthesisStatus,
)
from .mesh import (
    DirectorMeshError,
    conflict_result_to_mapping,
    director_assessment_to_mapping,
    director_synthesis_to_mapping,
)
from .registry import director_charter_to_mapping


DirectorArtifact = DirectorCharter | DirectorAssessment | ConflictResult | DirectorSynthesis


def _schema_mapping(document: Mapping[str, object]) -> dict[str, object]:
    try:
        payload = dict(validate_json_mapping(document))
    except JsonInputError as error:
        raise DirectorMeshError(error.code.value, error.detail) from error
    result = validate_artifact_mapping(payload)
    if not result.ok:
        first = result.errors[0]
        raise DirectorMeshError(
            first.reason_code or f"director.schema.{first.validator}", first.as_text()
        )
    return payload


def _reference(value: object) -> ArtifactReference:
    item = cast(dict[str, object], value)
    return ArtifactReference(
        path=RelativeArtifactPath(str(item["path"])),
        sha256=HashDigest(str(item["sha256"])),
        artifact_version=ArtifactVersion(str(item["artifact_version"])),
    )


def _references(value: object) -> tuple[ArtifactReference, ...]:
    return tuple(_reference(item) for item in cast(list[object], value))


def _assert_exact(
    payload: Mapping[str, object], rendered: Mapping[str, object], label: str
) -> None:
    if dict(payload) != dict(rendered):
        raise DirectorMeshError(
            f"director.{label}.canonical", f"{label} mapping is not canonical"
        )


def director_charter_from_mapping(document: Mapping[str, object]) -> DirectorCharter:
    payload = _schema_mapping(document)
    value = DirectorCharter(
        charter_id=OpaqueId(str(payload["charter_id"])),
        charter_sha256=HashDigest(str(payload["charter_sha256"])),
        director_id=OpaqueId(str(payload["director_id"])),
        director_version=str(payload["director_version"]),
        kind=DirectorKind(str(payload["kind"])),
        owned_patterns=tuple(cast(list[str], payload["owned_patterns"])),
        verified_patterns=tuple(cast(list[str], payload["verified_patterns"])),
        activation_signals=tuple(cast(list[str], payload["activation_signals"])),
        veto_patterns=tuple(cast(list[str], payload["veto_patterns"])),
        conflict_priority=int(payload["conflict_priority"]),
        rules_version=str(payload["rules_version"]),
    )
    _assert_exact(payload, director_charter_to_mapping(value), "charter")
    return value


def director_assessment_from_mapping(
    document: Mapping[str, object],
) -> DirectorAssessment:
    payload = _schema_mapping(document)
    patches = tuple(
        PatchProposal(
            field_path=str(item["field_path"]),
            proposal_kind=ProposalKind(str(item["proposal_kind"])),
            expected_value_sha256=HashDigest(str(item["expected_value_sha256"])),
            replacement_value_json=str(item["replacement_value_json"]),
            reason_code=str(item["reason_code"]),
            hard_constraint=bool(item["hard_constraint"]),
            evidence_refs=_references(item["evidence_refs"]),
        )
        for item in cast(list[dict[str, object]], payload["patches"])
    )
    blockers = tuple(
        DirectorBlocker(
            reason_code=str(item["reason_code"]),
            field_path=str(item["field_path"]),
            hard=bool(item["hard"]),
            evidence_refs=_references(item["evidence_refs"]),
        )
        for item in cast(list[dict[str, object]], payload["blockers"])
    )
    value = DirectorAssessment(
        assessment_id=OpaqueId(str(payload["assessment_id"])),
        assessment_sha256=HashDigest(str(payload["assessment_sha256"])),
        task_id=OpaqueId(str(payload["task_id"])),
        task_plan_sha256=HashDigest(str(payload["task_plan_sha256"])),
        director_id=OpaqueId(str(payload["director_id"])),
        director_version=str(payload["director_version"]),
        charter_sha256=HashDigest(str(payload["charter_sha256"])),
        registry_sha256=HashDigest(str(payload["registry_sha256"])),
        activation_id=OpaqueId(str(payload["activation_id"])),
        activation_sha256=HashDigest(str(payload["activation_sha256"])),
        activation_policy_sha256=HashDigest(
            str(payload["activation_policy_sha256"])
        ),
        episode_intent_sha256=HashDigest(str(payload["episode_intent_sha256"])),
        base_blueprint_sha256=HashDigest(str(payload["base_blueprint_sha256"])),
        blueprint_context_sha256=HashDigest(
            str(payload["blueprint_context_sha256"])
        ),
        model_id=OpaqueId(str(payload["model_id"])),
        prompt_charter_version=str(payload["prompt_charter_version"]),
        request_sha256=HashDigest(str(payload["request_sha256"])),
        response_sha256=HashDigest(str(payload["response_sha256"])),
        execution_receipt=_reference(payload["execution_receipt"]),
        verdict=DirectorVerdict(str(payload["verdict"])),
        patches=patches,
        blockers=blockers,
        recommendations=tuple(cast(list[str], payload["recommendations"])),
        confidence_basis_points=int(payload["confidence_basis_points"]),
        assumptions=tuple(cast(list[str], payload["assumptions"])),
        evidence_refs=_references(payload["evidence_refs"]),
    )
    _assert_exact(payload, director_assessment_to_mapping(value), "assessment")
    return value


def conflict_result_from_mapping(document: Mapping[str, object]) -> ConflictResult:
    payload = _schema_mapping(document)
    selected_id = payload["selected_assessment_id"]
    selected_sha = payload["selected_replacement_sha256"]
    previous_sha = payload["previous_synthesis_sha256"]
    value = ConflictResult(
        conflict_id=OpaqueId(str(payload["conflict_id"])),
        conflict_sha256=HashDigest(str(payload["conflict_sha256"])),
        base_blueprint_sha256=HashDigest(str(payload["base_blueprint_sha256"])),
        blueprint_context_sha256=HashDigest(
            str(payload["blueprint_context_sha256"])
        ),
        conflict_session_id=OpaqueId(str(payload["conflict_session_id"])),
        previous_synthesis_sha256=(
            HashDigest(str(previous_sha)) if previous_sha is not None else None
        ),
        field_path=str(payload["field_path"]),
        proposal_assessment_ids=tuple(
            OpaqueId(str(item))
            for item in cast(list[str], payload["proposal_assessment_ids"])
        ),
        status=ConflictStatus(str(payload["status"])),
        selected_assessment_id=(
            OpaqueId(str(selected_id)) if selected_id is not None else None
        ),
        rejected_assessment_ids=tuple(
            OpaqueId(str(item))
            for item in cast(list[str], payload["rejected_assessment_ids"])
        ),
        selected_replacement_sha256=(
            HashDigest(str(selected_sha)) if selected_sha is not None else None
        ),
        reason_code=str(payload["reason_code"]),
        rounds_used=int(payload["rounds_used"]),
        evidence_refs=_references(payload["evidence_refs"]),
    )
    _assert_exact(payload, conflict_result_to_mapping(value), "conflict")
    return value


def director_synthesis_from_mapping(
    document: Mapping[str, object],
) -> DirectorSynthesis:
    payload = _schema_mapping(document)
    result_sha = payload["resulting_blueprint_sha256"]
    previous_sha = payload["previous_synthesis_sha256"]
    value = DirectorSynthesis(
        synthesis_id=OpaqueId(str(payload["synthesis_id"])),
        synthesis_sha256=HashDigest(str(payload["synthesis_sha256"])),
        base_blueprint_sha256=HashDigest(str(payload["base_blueprint_sha256"])),
        blueprint_context_sha256=HashDigest(
            str(payload["blueprint_context_sha256"])
        ),
        conflict_session_id=OpaqueId(str(payload["conflict_session_id"])),
        previous_synthesis_sha256=(
            HashDigest(str(previous_sha)) if previous_sha is not None else None
        ),
        assessment_sha256s=tuple(
            HashDigest(str(item))
            for item in cast(list[str], payload["assessment_sha256s"])
        ),
        conflict_sha256s=tuple(
            HashDigest(str(item))
            for item in cast(list[str], payload["conflict_sha256s"])
        ),
        status=SynthesisStatus(str(payload["status"])),
        resulting_blueprint_sha256=(
            HashDigest(str(result_sha)) if result_sha is not None else None
        ),
        unresolved_blockers=tuple(
            cast(list[str], payload["unresolved_blockers"])
        ),
        rounds_used=int(payload["rounds_used"]),
    )
    _assert_exact(payload, director_synthesis_to_mapping(value), "synthesis")
    return value


def director_artifact_from_mapping(document: Mapping[str, object]) -> DirectorArtifact:
    version = document.get("artifact_version")
    parsers = {
        "director-charter/1.0": director_charter_from_mapping,
        "director-assessment/1.0": director_assessment_from_mapping,
        "blueprint-conflict/1.0": conflict_result_from_mapping,
        "director-synthesis/1.0": director_synthesis_from_mapping,
    }
    parser = parsers.get(version) if isinstance(version, str) else None
    if parser is None:
        raise DirectorMeshError(
            "director.artifact.version", "unsupported Director artifact version"
        )
    return parser(document)


def director_artifact_from_bytes(document: bytes) -> DirectorArtifact:
    try:
        mapping = require_json_object(parse_json_bytes(document))
    except JsonInputError as error:
        raise DirectorMeshError(error.code.value, error.detail) from error
    return director_artifact_from_mapping(mapping)


def director_artifact_to_mapping(value: DirectorArtifact) -> dict[str, object]:
    if isinstance(value, DirectorCharter):
        return director_charter_to_mapping(value)
    if isinstance(value, DirectorAssessment):
        return director_assessment_to_mapping(value)
    if isinstance(value, ConflictResult):
        return conflict_result_to_mapping(value)
    if isinstance(value, DirectorSynthesis):
        return director_synthesis_to_mapping(value)
    raise DirectorMeshError(
        "director.artifact.type", "unsupported Director artifact type"
    )


def director_artifact_to_bytes(value: DirectorArtifact) -> bytes:
    return canonical_json_bytes(director_artifact_to_mapping(value))


__all__ = [
    "DirectorArtifact",
    "conflict_result_from_mapping",
    "director_artifact_from_bytes",
    "director_artifact_from_mapping",
    "director_artifact_to_bytes",
    "director_artifact_to_mapping",
    "director_assessment_from_mapping",
    "director_charter_from_mapping",
    "director_synthesis_from_mapping",
]
